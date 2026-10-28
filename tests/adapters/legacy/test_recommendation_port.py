"""Tests for application port adaptation over the legacy recommendation bridge."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from hslegend.adapters.legacy.recommendation import RecommendationAdapter
from hslegend.application.recommendation import RecommendationContext
from hslegend.application.recommendation_ports import RecommendationAdapterPort
from hslegend.domain import ActionKind, ProposedAction, SlotRef
from tests.domain.support import snapshot


class RecommendationAdapterPortTests(unittest.TestCase):
    def test_legacy_adapter_satisfies_port_and_returns_declarative_result(self) -> None:
        adapter = RecommendationAdapter()
        self.assertIsInstance(adapter, RecommendationAdapterPort)
        proposed = ProposedAction(
            action_id="a1",
            frame_id="f1",
            created_at=0.0,
            turn_number=5,
            log_revision=7,
            raw_instruction="结束回合",
            normalized_instruction="结束回合",
            action=ActionKind.END_TURN,
        )
        result = adapter.adapt(
            proposed,
            RecommendationContext(snapshot(), "结束回合", evidence_id="ocr-1", log_revision=7),
        )
        self.assertEqual("end_turn", result.action_kind)
        self.assertEqual("turn_changed", result.postcondition)
        self.assertEqual("EndTurnAction", type(result.details[0][1]).__name__)


    def test_legacy_flow_call_compatibility_is_preserved(self) -> None:
        adapter = RecommendationAdapter()
        proposed = ProposedAction(
            action_id="a2",
            frame_id="f2",
            created_at=0.0,
            turn_number=5,
            log_revision=7,
            raw_instruction="结束回合",
            normalized_instruction="结束回合",
            action=ActionKind.END_TURN,
        )
        state = SimpleNamespace(game_num_turns_in_play=5)
        adapted = adapter(proposed, state)
        self.assertEqual("turn_changed", adapted.postcondition)
        self.assertEqual("EndTurnAction", type(adapted.manual_action).__name__)


if __name__ == "__main__":
    unittest.main()
