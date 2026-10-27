from __future__ import annotations

import unittest

from hslegend.domain import (
    ActionIntent,
    ActionPlan,
    ActionVerdict,
    Condition,
    PlanStep,
    PostconditionResult,
    ValidationResult,
)
from tests.domain.support import snapshot


class ResultContractTests(unittest.TestCase):
    def test_validation_result_helpers(self) -> None:
        accepted = ValidationResult.allow(("ok",))
        rejected = ValidationResult.reject("invalid_target", "missing")
        self.assertTrue(accepted.accepted)
        self.assertTrue(bool(accepted))
        self.assertFalse(rejected.accepted)
        self.assertEqual("invalid_target", rejected.reason_code)
        self.assertEqual(("missing",), rejected.details)

    def test_postcondition_and_action_verdict_helpers(self) -> None:
        confirmed = PostconditionResult.confirm("after")
        unconfirmed = PostconditionResult.unconfirmed("snapshot_unchanged", "a", "b")
        failed = PostconditionResult.fail(
            "postcondition_failed", "a", failed_condition_indexes=(1,)
        )
        self.assertTrue(confirmed.confirmed)
        self.assertEqual("unconfirmed", unconfirmed.status)
        self.assertEqual((1,), failed.failed_condition_indexes)
        self.assertTrue(ActionVerdict.from_postcondition(confirmed).accepted)
        self.assertFalse(
            ActionVerdict.from_validation(
                ValidationResult.reject("empty_plan")
            ).accepted
        )

    def test_plan_serialization_contains_declarative_conditions_only(self) -> None:
        game = snapshot()
        plan = ActionPlan(
            plan_id="plan-1",
            snapshot_id=game.snapshot_id,
            snapshot_revision=game.log_revision,
            game_generation=game.game_generation,
            intent=ActionIntent("end_turn"),
            steps=(
                PlanStep(
                    "step-1",
                    ActionIntent("end_turn"),
                    postconditions=(Condition("is_end", expected=True),),
                ),
            ),
        )
        value = plan.to_primitive()
        self.assertEqual("plan-1", value["plan_id"])
        self.assertIsInstance(value["steps"], list)
        self.assertEqual("is_end", value["steps"][0]["postconditions"][0]["kind"])
        self.assertNotIn("x", value)
        self.assertNotIn("y", value)
        self.assertNotIn("callback", value)


if __name__ == "__main__":
    unittest.main()
