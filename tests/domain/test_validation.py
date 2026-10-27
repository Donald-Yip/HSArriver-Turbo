from __future__ import annotations

import math
import unittest

from hslegend.domain import (
    ActionIntent,
    ActionPlan,
    CardCapabilitySet,
    CardDefinition,
    Condition,
    PlanRuleValidator,
    PlanStep,
    SUPPORTED_SCHEMA_VERSIONS,
)
from tests.domain.support import (
    FRIENDLY_BOARD,
    FRIENDLY_HAND,
    FRIENDLY_HERO,
    snapshot,
)


class PlanRuleValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = snapshot()
        self.validator = PlanRuleValidator()

    def valid_plan(self, **overrides: object) -> ActionPlan:
        values = {
            "plan_id": "plan-1",
            "snapshot_id": self.snapshot.snapshot_id,
            "snapshot_revision": self.snapshot.log_revision,
            "game_generation": self.snapshot.game_generation,
            "intent": ActionIntent(
                "play_card",
                source=FRIENDLY_HAND,
                target=FRIENDLY_BOARD,
                card_id="card_hand",
            ),
            "steps": (
                PlanStep(
                    "step-1",
                    ActionIntent(
                        "play_card",
                        source=FRIENDLY_HAND,
                        target=FRIENDLY_BOARD,
                        card_id="card_hand",
                    ),
                    preconditions=(
                        Condition("entity_exists", entity_id=FRIENDLY_HAND.entity_id),
                        Condition("is_my_turn", expected=True),
                    ),
                ),
            ),
            "timeout_seconds": 3.0,
        }
        values.update(overrides)
        return ActionPlan(**values)  # type: ignore[arg-type]

    def test_accepts_valid_non_empty_plan(self) -> None:
        result = self.validator.validate(self.valid_plan(), self.snapshot)
        self.assertTrue(result.accepted)
        self.assertEqual("accepted", result.reason_code)
        self.assertTrue(result)

    def test_game_generation_is_required(self) -> None:
        with self.assertRaises(TypeError):
            ActionPlan(  # type: ignore[call-arg]
                plan_id="plan-1",
                snapshot_id=self.snapshot.snapshot_id,
                snapshot_revision=self.snapshot.log_revision,
                intent=ActionIntent("end_turn"),
                steps=(PlanStep("step-1", ActionIntent("end_turn")),),
            )
        with self.assertRaisesRegex(TypeError, "game_generation must be an int"):
            self.valid_plan(game_generation=None)
        with self.assertRaisesRegex(TypeError, "game_generation must be an int"):
            self.valid_plan(game_generation=True)

    def test_rejects_empty_plan(self) -> None:
        result = self.validator.validate(self.valid_plan(steps=()), self.snapshot)
        self.assertFalse(result.accepted)
        self.assertEqual("empty_plan", result.reason_code)

    def test_rejects_stale_snapshot_id_revision_or_generation(self) -> None:
        cases = (
            {"snapshot_id": "old-snapshot"},
            {"snapshot_revision": self.snapshot.log_revision - 1},
            {"game_generation": self.snapshot.game_generation - 1},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                result = self.validator.validate(
                    self.valid_plan(**overrides), self.snapshot
                )
                self.assertEqual("stale_snapshot", result.reason_code)

    def test_fails_closed_on_unknown_or_mismatched_schema(self) -> None:
        self.assertEqual(frozenset({1}), SUPPORTED_SCHEMA_VERSIONS)
        result = self.validator.validate(
            self.valid_plan(schema_version=2), self.snapshot
        )
        self.assertEqual("unsupported_plan_schema", result.reason_code)

        unsupported_snapshot = snapshot(schema_version=2)
        result = self.validator.validate(self.valid_plan(), unsupported_snapshot)
        self.assertEqual("unsupported_snapshot_schema", result.reason_code)

        result = self.validator.validate(
            self.valid_plan(schema_version=2), unsupported_snapshot
        )
        self.assertEqual("unsupported_plan_schema", result.reason_code)

    def test_rejects_missing_or_changed_target(self) -> None:
        missing = ActionIntent(
            "play_card",
            source=FRIENDLY_HAND,
            card_id="card_hand",
            target=FRIENDLY_HERO.__class__(999, "x", "enemy", "board", 1),
        )
        plan = self.valid_plan(intent=missing, steps=(PlanStep("s", missing),))
        result = self.validator.validate(plan, self.snapshot)
        self.assertEqual("invalid_target", result.reason_code)
        self.assertIn("entity_not_found:999", result.details)

        changed = FRIENDLY_HERO.__class__(
            FRIENDLY_BOARD.entity_id,
            FRIENDLY_BOARD.card_id,
            FRIENDLY_BOARD.side,
            "hand",
            FRIENDLY_BOARD.zone_pos,
        )
        plan = self.valid_plan(
            intent=ActionIntent("play_card", source=FRIENDLY_HAND, target=changed),
            steps=(PlanStep("s", ActionIntent("play_card", target=changed)),),
        )
        result = self.validator.validate(plan, self.snapshot)
        self.assertEqual("invalid_target", result.reason_code)
        self.assertIn("entity_changed:11", result.details)

    def test_rejects_invalid_source(self) -> None:
        bad_source = FRIENDLY_HERO.__class__(777, "x", "friendly", "hand", 1)
        plan = self.valid_plan(
            intent=ActionIntent("play_card", source=bad_source),
            steps=(PlanStep("s", ActionIntent("play_card", source=bad_source)),),
        )
        result = self.validator.validate(plan, self.snapshot)
        self.assertEqual("invalid_source", result.reason_code)

    def test_rejects_non_finite_or_non_positive_timeouts(self) -> None:
        for timeout in (0, -1, float("nan"), float("inf"), float("-inf")):
            with self.subTest(plan_timeout=timeout):
                result = self.validator.validate(
                    self.valid_plan(timeout_seconds=timeout), self.snapshot
                )
                self.assertEqual("invalid_timeout", result.reason_code)

            with self.subTest(step_timeout=timeout):
                step = PlanStep(
                    "step-1",
                    ActionIntent("end_turn"),
                    timeout_seconds=timeout,
                )
                result = self.validator.validate(
                    self.valid_plan(steps=(step,)), self.snapshot
                )
                self.assertEqual("invalid_timeout", result.reason_code)

    def test_rejects_duplicate_step_id_and_failed_precondition(self) -> None:
        duplicate = self.valid_plan(
            steps=(
                PlanStep("same", ActionIntent("end_turn")),
                PlanStep("same", ActionIntent("end_turn")),
            )
        )
        self.assertEqual(
            "duplicate_step_id",
            self.validator.validate(duplicate, self.snapshot).reason_code,
        )
        false_precondition = PlanStep(
            "s",
            ActionIntent("end_turn"),
            preconditions=(Condition("is_end", expected=True),),
        )
        result = self.validator.validate(
            self.valid_plan(steps=(false_precondition,)), self.snapshot
        )
        self.assertEqual("precondition_failed", result.reason_code)

    def test_rejects_unsupported_precondition(self) -> None:
        plan = self.valid_plan(
            preconditions=(Condition("future_unknown_condition"),)
        )
        result = self.validator.validate(plan, self.snapshot)
        self.assertEqual("unsupported_condition", result.reason_code)

    def test_validates_step_card_against_plan_intent_and_definition(self) -> None:
        mismatched_step = PlanStep(
            "step-1",
            ActionIntent(
                "play_card",
                source=FRIENDLY_HAND,
                target=FRIENDLY_BOARD,
                card_id="other-card",
            ),
        )
        result = self.validator.validate(
            self.valid_plan(steps=(mismatched_step,)), self.snapshot
        )
        self.assertEqual("step_card_mismatch", result.reason_code)
        self.assertEqual(("step-1", "card_hand"), result.details)

        card = CardDefinition("other-card", capabilities=CardCapabilitySet())
        result = self.validator.validate(self.valid_plan(), self.snapshot, card=card)
        self.assertEqual("card_mismatch", result.reason_code)

        plan_without_card = self.valid_plan(
            intent=ActionIntent("end_turn"),
            steps=(mismatched_step,),
        )
        result = self.validator.validate(
            plan_without_card,
            self.snapshot,
            card=CardDefinition("card_hand"),
        )
        self.assertEqual("step_card_mismatch", result.reason_code)

        matching = CardDefinition("card_hand")
        self.assertTrue(
            self.validator.validate(
                self.valid_plan(), self.snapshot, card=matching
            ).accepted
        )


if __name__ == "__main__":
    unittest.main()
