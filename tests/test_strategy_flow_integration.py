# -*- coding: utf-8 -*-
"""End-to-end local path: real planner + real adapter on a replayed game state.

Uses the zoo turn-4 fixture (a serialized real ``StrategyState``) to prove the
seam between the ported search stack, the legacy planner adapter and the
execution adapter: the flow must produce a real ``PlayCardAction`` for
``TIME_025`` at hand slot 6 without any OCR.
"""

import unittest
from types import SimpleNamespace

import ai.deck_strategy as deck_strategy
from hslegend.adapters.legacy.recommendation import RecommendationAdapter
from hslegend.adapters.legacy.strategy_planner import LegacyStrategyPlanner
from hslegend.application.recommendation_flow import RecommendationFlow
from search.search import use_strategy
from tests.support.strategy_fixtures import load_fixture


class _NoCapture:
    def capture(self, ocr_panel_ok=False):
        raise AssertionError("local path must not capture the screen")

    @staticmethod
    def crop_recommendation(frame):
        raise AssertionError("local path must not crop")


class _NoReader:
    def read(self, *args, **kwargs):
        raise AssertionError("local path must not OCR")

    def read_frame(self, *args, **kwargs):
        raise AssertionError("local path must not OCR")


class _Controller:
    def __init__(self):
        self.executed = []

    @staticmethod
    def output(_message):
        pass

    def execute(self, action, _state):
        self.executed.append(action)
        return SimpleNamespace(executed=True, cancelled=False,
                               recovery_needed=False)


class StrategyFlowIntegrationTests(unittest.TestCase):
    def test_zoo_fixture_executes_play_card_without_ocr(self):
        _data, state = load_fixture("zoo_turn4.json")
        revision = state.log_revision
        planner = LegacyStrategyPlanner(time_limit=5.0)
        controller = _Controller()

        flow = RecommendationFlow(
            capture=_NoCapture(),
            reader=_NoReader(),
            parser=None,
            state_supplier=lambda: (state, revision),
            adapter=RecommendationAdapter(),
            validator=None,
            controller=controller,
            sleep=lambda _seconds: None,
            box_ai=False,
            planner=planner,
            strategy_name="动物园术",
        )

        strategy = deck_strategy.deck_strategy("动物园术")
        with use_strategy(strategy):
            result = flow.run_player_step() if hasattr(
                flow, "run_player_step") else flow.run_player_turn_step()

        self.assertEqual(1, len(controller.executed))
        action = controller.executed[0]
        self.assertEqual("TIME_025", action.card_id)
        self.assertEqual(5, action.hand_index)   # 0-based for hand slot 6
        self.assertIsNotNone(flow.last_plan)
        self.assertEqual(11.2, flow.last_plan["score"])
        self.assertIn("暮光时空跃迁者", flow.last_plan["steps"][0])
        self.assertIn(result.status.value, ("observe", "executed"))


if __name__ == "__main__":
    unittest.main()
