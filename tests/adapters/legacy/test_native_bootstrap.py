"""Native bootstrap graph tests using only injected port doubles."""

from __future__ import annotations

import unittest

from hslegend.adapters.demo import DemoScenario
from hslegend.adapters.legacy import NativePorts
from hslegend.application import ConfigSnapshot, OneGameCommand, OneGameStatus, OneGameUseCase
from hslegend.bootstrap import build_native_one_game


class _ConfigRepository:
    def load(self) -> ConfigSnapshot:
        return ConfigSnapshot()


class _ArtifactStore:
    def store(self, source_ref: str, *, kind: str = "evidence") -> str:
        return f"{kind}:{source_ref}"


class NativeBootstrapTests(unittest.TestCase):
    def test_builds_real_graph_from_injected_native_ports(self) -> None:
        scenario = DemoScenario.build()
        seen = {}

        def ports_builder(token, clock, sleep):
            seen["token"] = token
            seen["clock"] = clock
            seen["sleep"] = sleep
            return NativePorts(
                config_repository=_ConfigRepository(),
                card_catalog=scenario.catalog,
                recommendation_mulligan=scenario.recommendation,
                recommendation_turn=scenario.recommendation,
                game_state=scenario.state,
                lobby_state=scenario.lobby,
                action_dispatch=scenario.dispatch,
                journal=scenario.journal,
                artifact_store=_ArtifactStore(),
            )

        use_case = build_native_one_game(
            ports_builder=ports_builder,
            clock=lambda: 0.0,
            sleep=lambda _seconds: None,
        )

        self.assertIsInstance(use_case, OneGameUseCase)
        self.assertTrue(hasattr(seen["token"], "is_cancelled"))
        self.assertEqual(0.0, seen["clock"]())

        result = use_case.execute(OneGameCommand("native-graph"))
        self.assertIs(OneGameStatus.COMPLETED, result.status)
        self.assertEqual(1, result.turns_played)
        self.assertEqual(1, result.actions_played)


if __name__ == "__main__":
    unittest.main()
