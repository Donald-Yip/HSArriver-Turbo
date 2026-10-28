"""Tests for application recommendation contracts."""

from __future__ import annotations

import unittest

from hslegend.application.recommendation import AdaptedAction, RecommendationContext
from tests.domain.support import snapshot


class RecommendationContractTests(unittest.TestCase):
    def test_context_keeps_evidence_pinned_instruction(self) -> None:
        context = RecommendationContext(
            snapshot=snapshot(), instruction="结束回合", evidence_id="ocr-1", log_revision=7
        )
        self.assertEqual("结束回合", context.instruction)
        self.assertEqual("ocr-1", context.evidence_id)

    def test_adapted_action_is_frozen_and_declarative(self) -> None:
        action = AdaptedAction("end_turn", postcondition="turn_changed")
        self.assertEqual("end_turn", action.action_kind)
        self.assertEqual("turn_changed", action.postcondition)
        with self.assertRaises(AttributeError):
            action.action_kind = "attack"


if __name__ == "__main__":
    unittest.main()
