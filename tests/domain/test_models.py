from __future__ import annotations

import dataclasses
import json
import unittest

from hslegend.domain import (
    ActionIntent,
    ActionPlan,
    ActionVerdict,
    CardCapabilitySet,
    CardDefinition,
    ChoiceView,
    Condition,
    EntityRef,
    GameSnapshot,
    PlanStep,
    PlayerView,
    PostconditionResult,
    ValidationResult,
)
from tests.domain.support import (
    FRIENDLY_BOARD,
    FRIENDLY_HAND,
    FRIENDLY_HERO,
    OPPONENT_HERO,
    friendly_player,
    snapshot,
)


MODEL_TYPES = (
    ActionIntent,
    ActionPlan,
    ActionVerdict,
    CardCapabilitySet,
    CardDefinition,
    ChoiceView,
    Condition,
    EntityRef,
    GameSnapshot,
    PlanStep,
    PlayerView,
    PostconditionResult,
    ValidationResult,
)


class ModelContractTests(unittest.TestCase):
    def test_all_domain_models_are_frozen_slots_dataclasses(self) -> None:
        for model_type in MODEL_TYPES:
            with self.subTest(model=model_type.__name__):
                self.assertTrue(dataclasses.is_dataclass(model_type))
                self.assertTrue(model_type.__dataclass_params__.frozen)
                self.assertIsNotNone(model_type.__slots__)
                self.assertNotIn("__dict__", model_type.__slots__)

    def test_entity_and_snapshot_cannot_be_mutated(self) -> None:
        entity = FRIENDLY_HAND
        with self.assertRaises(dataclasses.FrozenInstanceError):
            entity.zone = "board"  # type: ignore[misc]

        game = snapshot()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            game.turn_number = 6  # type: ignore[misc]

    def test_collection_inputs_are_frozen_to_tuples(self) -> None:
        player = PlayerView(
            player_id=1,
            side="friendly",
            hand=[FRIENDLY_HAND],  # type: ignore[arg-type]
            board=[FRIENDLY_BOARD],  # type: ignore[arg-type]
        )
        self.assertIsInstance(player.hand, tuple)
        self.assertIsInstance(player.board, tuple)
        self.assertEqual((FRIENDLY_HAND,), player.hand)

        step = PlanStep(
            "step-1",
            ActionIntent("play"),
            preconditions=[Condition("is_end", expected=False)],  # type: ignore[arg-type]
        )
        self.assertIsInstance(step.preconditions, tuple)
        self.assertIsInstance(step.postconditions, tuple)
        self.assertIsInstance(step.preconditions[0], Condition)

    def test_snapshot_entity_views_are_deduplicated_and_deterministic(self) -> None:
        game = snapshot()
        ids = [entity.entity_id for entity in game.all_entities()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(FRIENDLY_HERO, game.entity(FRIENDLY_HERO.entity_id))
        friendly = game.entities_for_side("FRIENDLY")
        self.assertIn(FRIENDLY_HAND, friendly)
        self.assertNotIn(OPPONENT_HERO, friendly)
        self.assertIn(FRIENDLY_BOARD, game.entities_in_zone("BOARD"))
        self.assertIsNone(game.entity(999))

    def test_snapshot_rejects_duplicate_entity_ids(self) -> None:
        duplicate = EntityRef(
            FRIENDLY_HAND.entity_id,
            "duplicate_card",
            "friendly",
            "board",
            2,
        )
        player = dataclasses.replace(
            friendly_player(), board=(FRIENDLY_BOARD, duplicate)
        )
        with self.assertRaisesRegex(ValueError, "duplicate entity_id: 10"):
            dataclasses.replace(snapshot(), my_player=player)

        duplicate_choice = dataclasses.replace(
            snapshot().choices[0], options=(FRIENDLY_HAND,)
        )
        with self.assertRaisesRegex(ValueError, "duplicate entity_id: 10"):
            dataclasses.replace(snapshot(), choices=(duplicate_choice,))

    def test_condition_expected_is_deep_frozen(self) -> None:
        source = {"values": [1, {"nested": ["x"]}]}
        condition = Condition("is_end", expected=source)
        source["values"].append(3)
        source["values"][1]["nested"].append("y")
        self.assertEqual(
            (("values", (1, (("nested", ("x",)),))),),
            condition.expected,
        )
        with self.assertRaises(AttributeError):
            condition.expected[0][1][1][0][1].append("z")  # type: ignore[union-attr]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            condition.expected = None  # type: ignore[misc]
        with self.assertRaises(TypeError):
            Condition("is_end", expected=object())

    def test_primitive_and_canonical_json_serialization_are_deterministic(self) -> None:
        game = snapshot()
        first = game.to_primitive()
        second = game.to_primitive()
        self.assertEqual(first, second)
        self.assertEqual(game.to_json(), game.to_json())
        self.assertEqual(first["choices"][0]["options"][0]["entity_id"], 30)
        self.assertIsInstance(first["choices"], list)
        self.assertEqual(json.loads(game.to_json()), first)

    def test_card_contracts_serialize_static_capabilities(self) -> None:
        card = CardDefinition(
            "card-1",
            "Targeted Card",
            2,
            "spell",
            "Deal 1 damage.",
            CardCapabilitySet(targetable=True, hand_target=True),
        )
        value = card.to_primitive()
        self.assertTrue(value["capabilities"]["targetable"])
        self.assertTrue(value["capabilities"]["hand_target"])
        self.assertFalse(value["capabilities"]["timeline"])
        self.assertIn('"card_id":"card-1"', card.to_json())


if __name__ == "__main__":
    unittest.main()
