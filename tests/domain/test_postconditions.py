from __future__ import annotations

import unittest

from hslegend.domain import Condition, PostconditionEvaluator, SUPPORTED_SCHEMA_VERSIONS
from tests.domain.support import FRIENDLY_HAND, snapshot


class PostconditionEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.before = snapshot(snapshot_id="before", revision=10, turn=4, mana=3)
        self.after = snapshot(snapshot_id="after", revision=11, turn=5, mana=5)
        self.evaluator = PostconditionEvaluator()

    def test_confirms_matching_postconditions_on_advanced_snapshot(self) -> None:
        result = self.evaluator.evaluate(
            self.before,
            self.after,
            (
                Condition("turn_number", expected=5),
                Condition("player_mana", side="friendly", expected=5),
                Condition("revision_advanced"),
                Condition("snapshot_changed"),
            ),
        )
        self.assertTrue(result.confirmed)
        self.assertEqual("postcondition_confirmed", result.reason_code)
        self.assertEqual(("after",), result.evidence_refs)

    def test_unchanged_revision_is_unconfirmed_before_condition_evaluation(self) -> None:
        same = snapshot(snapshot_id="same", revision=10, turn=9, mana=1)
        result = self.evaluator.evaluate(
            self.before,
            same,
            (
                Condition("is_end", expected=True),
                Condition("not_a_supported_condition"),
            ),
        )
        self.assertEqual("unconfirmed", result.status)
        self.assertEqual("snapshot_unchanged", result.reason_code)
        self.assertEqual((), result.failed_condition_indexes)

    def test_regressed_revision_fails_closed_before_condition_evaluation(self) -> None:
        regressed = snapshot(snapshot_id="regressed", revision=9)
        result = self.evaluator.evaluate(
            self.before,
            regressed,
            (
                Condition("turn_number", expected=999),
                Condition("not_a_supported_condition"),
            ),
        )
        self.assertEqual("failed", result.status)
        self.assertEqual("snapshot_regressed", result.reason_code)
        self.assertEqual((), result.failed_condition_indexes)
        self.assertEqual(("before", "regressed"), result.evidence_refs)

    def test_reports_failed_condition_indexes_only_on_advanced_snapshot(self) -> None:
        result = self.evaluator.evaluate(
            self.before,
            self.after,
            (
                Condition("turn_number", expected=5),
                Condition("is_end", expected=True),
                Condition("entity_exists", entity_id=999),
            ),
        )
        self.assertEqual("failed", result.status)
        self.assertEqual("postcondition_failed", result.reason_code)
        self.assertEqual((1, 2), result.failed_condition_indexes)
        self.assertEqual(("before", "after"), result.evidence_refs)

    def test_fails_closed_on_schema_mismatch_or_unsupported_schema(self) -> None:
        self.assertEqual(frozenset({1}), SUPPORTED_SCHEMA_VERSIONS)
        mismatched = snapshot(
            schema_version=2, snapshot_id="after", revision=11
        )
        result = self.evaluator.evaluate(self.before, mismatched)
        self.assertEqual("failed", result.status)
        self.assertEqual("schema_mismatch", result.reason_code)

        unsupported_before = snapshot(
            schema_version=2, snapshot_id="before", revision=10
        )
        unsupported_after = snapshot(
            schema_version=2, snapshot_id="after", revision=11
        )
        result = self.evaluator.evaluate(unsupported_before, unsupported_after)
        self.assertEqual("failed", result.status)
        self.assertEqual("unsupported_snapshot_schema", result.reason_code)

    def test_fails_on_generation_or_unsupported_condition(self) -> None:
        other_game = snapshot(snapshot_id="other", revision=11, generation=4)
        self.assertEqual(
            "game_generation_changed",
            self.evaluator.evaluate(self.before, other_game).reason_code,
        )

        unsupported = self.evaluator.evaluate(
            self.before, self.after, (Condition("not_a_condition"),)
        )
        self.assertEqual("unsupported_condition", unsupported.reason_code)
        self.assertEqual((0,), unsupported.failed_condition_indexes)

    def test_entity_conditions_use_only_snapshot_data(self) -> None:
        same_hand = self.evaluator.evaluate(
            self.before,
            self.after,
            (
                Condition(
                    "entity_zone",
                    entity_id=FRIENDLY_HAND.entity_id,
                    expected="HAND",
                ),
            ),
        )
        self.assertTrue(same_hand.confirmed)
        absent = self.evaluator.evaluate(
            self.before,
            self.after,
            (Condition("entity_absent", entity_id=999),),
        )
        self.assertTrue(absent.confirmed)

    def test_evaluation_does_not_mutate_inputs(self) -> None:
        before_value = self.before.to_primitive()
        after_value = self.after.to_primitive()
        self.evaluator.evaluate(
            self.before,
            self.after,
            (Condition("turn_number", expected=5),),
        )
        self.assertEqual(before_value, self.before.to_primitive())
        self.assertEqual(after_value, self.after.to_primitive())


if __name__ == "__main__":
    unittest.main()
