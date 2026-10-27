from __future__ import annotations

import unittest

from hslegend.application import (
    ActionDispatchPort,
    ArtifactStorePort,
    CardCatalogPort,
    ConfigRepositoryPort,
    ConfigSnapshot,
    DispatchResult,
    ExecutionRecord,
    ExecutionStatus,
    GameObservation,
    GameStateSourcePort,
    LobbyPhase,
    LobbyState,
    LobbyStatePort,
    RecommendationMode,
    RecommendationObservation,
    RecommendationSourcePort,
    RunJournalPort,
)
from hslegend.domain import ActionIntent, CardCapabilitySet, CardDefinition, PlanStep
from tests.application.fakes import (
    ActionDispatchFake,
    ArtifactStoreFake,
    CardCatalogFake,
    ConfigRepositoryFake,
    GameStateSourceFake,
    LobbyStateSourceFake,
    RecommendationSourceFake,
    RunJournalFake,
)
from tests.domain.support import snapshot


class PortContractTests(unittest.TestCase):
    def test_deterministic_fakes_satisfy_runtime_checkable_ports(self) -> None:
        game = snapshot()
        observation = GameObservation(game, 1.0)
        cases = (
            (ConfigRepositoryPort, ConfigRepositoryFake()),
            (CardCatalogPort, CardCatalogFake()),
            (
                RecommendationSourcePort,
                RecommendationSourceFake(
                    {
                        RecommendationMode.TURN: RecommendationObservation(
                            RecommendationMode.TURN,
                            "end_turn",
                            1.0,
                            "evidence-1",
                            game.snapshot_id,
                        )
                    }
                ),
            ),
            (GameStateSourcePort, GameStateSourceFake((observation,))),
            (
                LobbyStatePort,
                LobbyStateSourceFake(
                    (LobbyState(LobbyPhase.MAIN_MENU, game.observed_at),)
                ),
            ),
            (ActionDispatchPort, ActionDispatchFake((DispatchResult.sent("d-1"),))),
            (RunJournalPort, RunJournalFake()),
            (ArtifactStorePort, ArtifactStoreFake()),
        )
        for port, fake in cases:
            with self.subTest(port=port.__name__):
                self.assertIsInstance(fake, port)

    def test_data_ports_return_domain_and_application_values(self) -> None:
        game = snapshot()
        card = CardDefinition(
            "card-1",
            "Card",
            2,
            "spell",
            capabilities=CardCapabilitySet(targetable=True),
        )
        config_fake = ConfigRepositoryFake(ConfigSnapshot({"deck": "deck-1"}))
        card_fake = CardCatalogFake((card,))
        game_fake = GameStateSourceFake((GameObservation(game, 0.9),))

        self.assertEqual("deck-1", config_fake.load().get("deck"))
        self.assertIs(card, card_fake.get_card("card-1"))
        self.assertIsNone(card_fake.get_card("missing"))
        self.assertEqual(game, game_fake.current().snapshot)
        self.assertEqual(
            game,
            game_fake.wait_for_revision_change(6, 0.25).snapshot,
        )
        self.assertEqual([(6, 0.25)], game_fake.wait_calls)

    def test_dispatch_and_journal_fakes_are_deterministic(self) -> None:
        game = snapshot()
        step = PlanStep("step-1", ActionIntent("end_turn"))
        dispatch_fake = ActionDispatchFake(
            (DispatchResult.sent("dispatch-1", "evidence-1"),)
        )
        result = dispatch_fake.dispatch(step, game, timeout_seconds=0.5)
        self.assertEqual("dispatch-1", result.dispatch_id)
        self.assertEqual([(step, game)], dispatch_fake.calls)

        journal_fake = RunJournalFake()
        record = ExecutionRecord(
            "record-1",
            "correlation-1",
            "session-1",
            "game-1",
            "intent-1",
            "plan-1",
            "action-1",
            "before",
            "after",
            ExecutionStatus.EXECUTED_UNCONFIRMED,
            evidence_ids=("evidence-1",),
        )
        first = journal_fake.append(record)
        second = journal_fake.append(record)
        self.assertEqual("journal-1", first.record_id)
        self.assertEqual("journal-2", second.record_id)
        self.assertEqual((record, record), journal_fake.records)


if __name__ == "__main__":
    unittest.main()
