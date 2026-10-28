"""Tests for recommendation adapter ports."""

from __future__ import annotations

import unittest

from hslegend.application.recommendation import AdaptedAction, RecommendationContext
from hslegend.application.recommendation_ports import (
    CardSemanticsPort,
    RecommendationAdapterPort,
)
from hslegend.domain import ActionIntent
from tests.domain.support import snapshot


class _Adapter:
    def adapt(self, intent, context):
        return AdaptedAction(intent.action_type, postcondition="done")


class _Catalog:
    def get_card(self, card_id):
        return None


class RecommendationPortTests(unittest.TestCase):
    def test_protocols_accept_narrow_implementations(self) -> None:
        self.assertIsInstance(_Adapter(), RecommendationAdapterPort)
        self.assertIsInstance(_Catalog(), CardSemanticsPort)

    def test_adapter_returns_declarative_result(self) -> None:
        result = _Adapter().adapt(
            ActionIntent("end_turn"),
            RecommendationContext(snapshot(), "结束回合"),
        )
        self.assertEqual("end_turn", result.action_kind)
        self.assertEqual("done", result.postcondition)


if __name__ == "__main__":
    unittest.main()
