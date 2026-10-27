from __future__ import annotations

import unittest

from hslegend.application.planning import (
    MissingCardDefinitionError,
    PlanBuilder,
    PluginNotFoundError,
)
from hslegend.domain import (
    ActionIntent,
    CardCapabilitySet,
    CardDefinition,
)
from hslegend.plugins import build_default_registry
from tests.application.fakes import CardCatalogFake
from tests.domain.support import FRIENDLY_BOARD, FRIENDLY_HAND, snapshot


class DefaultPluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = snapshot()
        self.cards = CardCatalogFake(
            (
                CardDefinition(
                    "plain-card",
                    capabilities=CardCapabilitySet(),
                ),
                CardDefinition(
                    "target-card",
                    capabilities=CardCapabilitySet(targetable=True, hand_target=True),
                ),
                CardDefinition(
                    "location-card",
                    capabilities=CardCapabilitySet(location=True),
                ),
            )
        )
        self.builder = PlanBuilder(build_default_registry(), self.cards)

    def build(self, intent: ActionIntent):
        return self.builder.build(self.snapshot, intent)

    def test_all_default_action_families_build_pinned_declarative_plans(self) -> None:
        cases = (
            ("mulligan", "mulligan", ActionIntent("mulligan_keep", intent_id="m")),
            (
                "play-card",
                "play-card",
                ActionIntent(
                    "play_card",
                    source=FRIENDLY_HAND,
                    card_id="plain-card",
                    intent_id="p",
                ),
            ),
            (
                "targeted-card",
                "targeted-card",
                ActionIntent(
                    "play_card",
                    source=FRIENDLY_HAND,
                    target=FRIENDLY_BOARD,
                    card_id="target-card",
                    intent_id="t",
                ),
            ),
            (
                "attack",
                "attack",
                ActionIntent(
                    "attack",
                    source=FRIENDLY_BOARD,
                    target=FRIENDLY_BOARD,
                    intent_id="a",
                ),
            ),
            (
                "hero-power",
                "hero-power",
                ActionIntent("hero_power", intent_id="h"),
            ),
            (
                "location",
                "location",
                ActionIntent(
                    "use_location",
                    source=FRIENDLY_BOARD,
                    card_id="location-card",
                    intent_id="l",
                ),
            ),
            ("end-turn", "end-turn", ActionIntent("end_turn", intent_id="e")),
        )

        for expected_prefix, plugin_id, intent in cases:
            with self.subTest(plugin_id=plugin_id):
                plan = self.build(intent)
                self.assertEqual(self.snapshot.snapshot_id, plan.snapshot_id)
                self.assertEqual(self.snapshot.log_revision, plan.snapshot_revision)
                self.assertEqual(intent, plan.intent)
                self.assertTrue(plan.steps)
                self.assertTrue(plan.plan_id.startswith(f"{expected_prefix}:"))
                self.assertEqual(intent, plan.steps[0].intent)

    def test_targeted_plugin_requires_target_and_attack_requires_both_entities(self) -> None:
        with self.assertRaises(ValueError):
            self.build(
                ActionIntent(
                    "play_card",
                    source=FRIENDLY_HAND,
                    card_id="target-card",
                    intent_id="missing-target",
                )
            )
        with self.assertRaises(ValueError):
            self.build(ActionIntent("attack", intent_id="missing-entities"))

    def test_missing_card_definition_fails_closed(self) -> None:
        with self.assertRaises(MissingCardDefinitionError):
            self.build(
                ActionIntent(
                    "play_card",
                    source=FRIENDLY_HAND,
                    card_id="not-in-catalog",
                    intent_id="missing-card",
                )
            )


if __name__ == "__main__":
    unittest.main()
