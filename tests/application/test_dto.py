from __future__ import annotations

import dataclasses
import json
import unittest

from hslegend.application import (
    ConfigSnapshot,
    DispatchResult,
    DispatchStatus,
    ExecutionRecord,
    ExecutionStatus,
    GameObservation,
    JournalAppendResult,
    LobbyPhase,
    LobbyState,
    OneGameCommand,
    OneGameResult,
    OneGameStatus,
    RecommendationMode,
    RecommendationObservation,
)
from tests.domain.support import snapshot


DTO_TYPES = (
    ConfigSnapshot,
    DispatchResult,
    ExecutionRecord,
    GameObservation,
    JournalAppendResult,
    LobbyState,
    OneGameCommand,
    OneGameResult,
    RecommendationObservation,
)


class DtoContractTests(unittest.TestCase):
    def test_all_dtos_are_frozen_slots_dataclasses(self) -> None:
        for dto_type in DTO_TYPES:
            with self.subTest(dto=dto_type.__name__):
                self.assertTrue(dataclasses.is_dataclass(dto_type))
                self.assertTrue(dto_type.__dataclass_params__.frozen)
                self.assertIsNotNone(dto_type.__slots__)

    def test_observations_are_immutable_and_serializable(self) -> None:
        game = snapshot()
        observation = GameObservation(game, 0.875)
        recommendation = RecommendationObservation(
            RecommendationMode.TURN,
            "end_turn",
            0.75,
            "evidence-1",
            game.snapshot_id,
        )
        lobby = LobbyState(LobbyPhase.IN_GAME, game.observed_at, 0.9, "evidence-2")

        with self.assertRaises(dataclasses.FrozenInstanceError):
            observation.source_confidence = 1.0  # type: ignore[misc]

        for value in (observation, recommendation, lobby):
            with self.subTest(value=type(value).__name__):
                encoded = value.to_json()
                self.assertEqual(value.to_primitive(), json.loads(encoded))
                self.assertEqual(encoded, value.to_json())

    def test_commands_results_and_execution_records_round_trip(self) -> None:
        command = OneGameCommand("command-1", "deck-1", max_turns=20)
        record = ExecutionRecord(
            record_id="record-1",
            correlation_id="correlation-1",
            session_id="session-1",
            game_id="game-1",
            intent_id="intent-1",
            plan_id="plan-1",
            action_id="action-1",
            before_snapshot_id="before",
            after_snapshot_id="after",
            status=ExecutionStatus.EXECUTED_CONFIRMED,
            step_results=("step-1:confirmed",),
            elapsed_seconds=0.25,
            evidence_ids=("evidence-1",),
        )
        result = OneGameResult(
            command_id=command.command_id,
            session_id="session-1",
            status=OneGameStatus.COMPLETED,
            game_id="game-1",
            journal_record_id="journal-1",
            evidence_ids=("evidence-1",),
        )
        dispatch = DispatchResult.sent("dispatch-1", "evidence-1")

        self.assertEqual("traditional", command.mode)
        self.assertEqual(DispatchStatus.SENT, dispatch.status)
        self.assertEqual((("step-1:confirmed",)), record.step_results)
        self.assertEqual("executed_confirmed", record.to_primitive()["status"])
        self.assertEqual("completed", result.to_primitive()["status"])
        self.assertEqual(json.loads(result.to_json()), result.to_primitive())

    def test_config_snapshot_freezes_nested_values(self) -> None:
        source = {"nested": [1, {"items": ["a"]}], "enabled": True}
        config = ConfigSnapshot(source)  # type: ignore[arg-type]
        source["nested"].append(2)

        self.assertEqual(True, config.get("enabled"))
        self.assertEqual((1, (("items", ("a",)),)), config.get("nested"))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.values = ()  # type: ignore[misc]
        self.assertEqual(json.loads(config.to_json()), config.to_primitive())

    def test_nested_non_finite_floats_are_rejected_at_construction(self) -> None:
        invalid_values = (
            {"nested": [1.0, {"bad": float("nan")}]},
            {"nested": {"bad": float("inf")}},
            {"nested": (float("-inf"),)},
        )
        for values in invalid_values:
            with self.subTest(values=repr(values)):
                with self.assertRaisesRegex(ValueError, "finite"):
                    ConfigSnapshot(values)  # type: ignore[arg-type]

    def test_max_turns_requires_an_exact_positive_int(self) -> None:
        for invalid in (True, False, 1.5, 2.0):
            with self.subTest(max_turns=invalid):
                with self.assertRaises(TypeError):
                    OneGameCommand("command", "deck", max_turns=invalid)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            OneGameCommand("command", "deck", max_turns=0)
        self.assertEqual(3, OneGameCommand("command", "deck", max_turns=3).max_turns)

    def test_invalid_confidence_and_status_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            GameObservation(snapshot(), 1.1)
        with self.assertRaises(ValueError):
            RecommendationObservation(
                RecommendationMode.MULLIGAN,
                "keep",
                -0.1,
                "evidence",
                "snapshot",
            )
        with self.assertRaises(ValueError):
            OneGameCommand("command", "deck", max_turns=0)


if __name__ == "__main__":
    unittest.main()
