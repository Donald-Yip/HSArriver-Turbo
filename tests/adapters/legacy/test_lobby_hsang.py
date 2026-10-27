"""Lobby precedence and stable HSAng bridge tests."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from log_op import LOG_CONTAINER_ERROR
from log_state import LogState

from hslegend.adapters.hsang import HsangObservationError, HsangReadError
from hslegend.adapters.legacy import (
    AuthoritativeLobbyStateAdapter,
    LegacyPowerLogPump,
    StableHsangRecommendationSource,
)
from hslegend.application import LobbyPhase, LobbyState, RecommendationMode
from tests.domain.support import snapshot

from .test_powerlog import _container, synthetic_state


class _UiState:
    def __init__(self, phase: LobbyPhase = LobbyPhase.MAIN_MENU) -> None:
        self.phase = phase
        self.calls = 0

    def get_state(self, *, timeout_seconds: float) -> LobbyState:
        del timeout_seconds
        self.calls += 1
        return LobbyState(self.phase, 1.0, 0.5, "ui-evidence")


class _ExplodingUi:
    def get_state(self, *, timeout_seconds: float) -> LobbyState:
        raise AssertionError("UI recognizer must not run when Power.log is authoritative")


class LobbyPrecedenceTests(unittest.TestCase):
    def test_complete_state_wins_over_contradictory_ui(self) -> None:
        state = synthetic_state(complete=True)
        pump = LegacyPowerLogPump(state=state, containers=[_container()], clock=lambda: 1.0)
        adapter = AuthoritativeLobbyStateAdapter(
            pump,
            _ExplodingUi(),  # type: ignore[arg-type]
            clock=lambda: 2.0,
        )

        result = adapter.get_state(timeout_seconds=1.0)

        self.assertIs(LobbyPhase.RESULT, result.phase)
        self.assertEqual(1.0, result.confidence)
        self.assertIn("result", result.evidence_id)

    def test_active_game_wins_without_ui(self) -> None:
        state = synthetic_state()
        pump = LegacyPowerLogPump(state=state, containers=[_container()], clock=lambda: 1.0)
        adapter = AuthoritativeLobbyStateAdapter(
            pump,
            _ExplodingUi(),  # type: ignore[arg-type]
            clock=lambda: 2.0,
        )

        result = adapter.get_state(timeout_seconds=1.0)

        self.assertIs(LobbyPhase.IN_GAME, result.phase)

    def test_unavailable_power_log_falls_back_to_ui(self) -> None:
        container = SimpleNamespace(
            log_type=LOG_CONTAINER_ERROR,
            session_id="session-1",
            message_list=[],
        )
        pump = LegacyPowerLogPump(
            state=LogState(),
            containers=[container],
            clock=lambda: 1.0,
        )
        ui = _UiState(LobbyPhase.DECK_SELECT)
        adapter = AuthoritativeLobbyStateAdapter(pump, ui, clock=lambda: 2.0)

        result = adapter.get_state(timeout_seconds=1.0)

        self.assertIs(LobbyPhase.DECK_SELECT, result.phase)
        self.assertEqual(1, ui.calls)
        self.assertEqual("power_log_unavailable", pump.last_error)

    def test_stale_active_state_with_container_error_falls_back_to_ui(self) -> None:
        state = synthetic_state()
        error = SimpleNamespace(
            log_type=LOG_CONTAINER_ERROR,
            session_id="session-1",
            message_list=[],
        )
        pump = LegacyPowerLogPump(
            state=state,
            containers=[error],
            clock=lambda: 1.0,
        )
        ui = _UiState(LobbyPhase.MAIN_MENU)
        adapter = AuthoritativeLobbyStateAdapter(pump, ui, clock=lambda: 2.0)

        result = adapter.get_state(timeout_seconds=1.0)

        self.assertIs(LobbyPhase.MAIN_MENU, result.phase)
        self.assertEqual(1, ui.calls)
        self.assertEqual("power_log_unavailable", pump.last_error)

    def test_successful_container_restores_power_log_authority(self) -> None:
        state = synthetic_state()
        error = SimpleNamespace(
            log_type=LOG_CONTAINER_ERROR,
            session_id="session-1",
            message_list=[],
        )
        pump = LegacyPowerLogPump(
            state=state,
            containers=[error, _container()],
            clock=lambda: 1.0,
        )
        ui = _UiState(LobbyPhase.MAIN_MENU)
        adapter = AuthoritativeLobbyStateAdapter(pump, ui, clock=lambda: 2.0)

        first = adapter.get_state(timeout_seconds=1.0)
        second = adapter.get_state(timeout_seconds=1.0)

        self.assertIs(LobbyPhase.MAIN_MENU, first.phase)
        self.assertIs(LobbyPhase.IN_GAME, second.phase)
        self.assertEqual(1, ui.calls)
        self.assertEqual("", pump.last_error)

    def test_ui_recognizer_supplies_menu_when_no_game_exists(self) -> None:
        state = synthetic_state()
        state.game_entity_id = 0
        state.entity_dict.pop("1")
        pump = LegacyPowerLogPump(state=state, containers=[_container()], clock=lambda: 1.0)
        ui = _UiState(LobbyPhase.MAIN_MENU)
        adapter = AuthoritativeLobbyStateAdapter(pump, ui, clock=lambda: 2.0)

        result = adapter.get_state(timeout_seconds=1.0)

        self.assertIs(LobbyPhase.MAIN_MENU, result.phase)
        self.assertEqual(1, ui.calls)


class StableHsangBridgeTests(unittest.TestCase):
    def _source(self, *, parsed_instruction: str = "结束回合"):
        captured = []
        parsed_calls = []
        roi_calls = []

        def capture():
            frame = SimpleNamespace(frame_id=f"frame-{len(captured) + 1}", exact_hash="hash-1")
            captured.append(frame)
            return frame

        class Reader:
            def read(self, frame_supplier, roi_supplier):
                for _ in range(3):
                    frame = frame_supplier()
                    roi_supplier(frame)
                return SimpleNamespace(
                    frame_id="ocr-frame",
                    normalized_text=parsed_instruction,
                    confidence=0.9,
                )

        def parse(ocr, turn_number, log_revision):
            parsed_calls.append((ocr, turn_number, log_revision))
            return SimpleNamespace(
                normalized_instruction=parsed_instruction,
                turn_number=turn_number,
                log_revision=log_revision,
                ocr_confidence=0.9,
                semantic_confidence=0.8,
            )

        def mulligan_roi(frame):
            roi_calls.append(("mulligan", frame.frame_id))
            return "mulligan-roi"

        def turn_roi(frame):
            roi_calls.append(("turn", frame.frame_id))
            return "turn-roi"

        source = StableHsangRecommendationSource(
            capture,
            Reader(),
            parse,
            mulligan_roi_supplier=mulligan_roi,
            turn_roi_supplier=turn_roi,
        )
        return source, captured, parsed_calls, roi_calls

    def test_reads_multiple_frames_and_parses_returned_evidence(self) -> None:
        source, captured, parsed_calls, roi_calls = self._source()
        expected = snapshot()

        observation = source.recommend(expected, RecommendationMode.TURN)

        self.assertEqual(3, len(captured))
        self.assertEqual(3, len(roi_calls))
        self.assertTrue(all(kind == "turn" for kind, _frame in roi_calls))
        self.assertEqual(1, len(parsed_calls))
        self.assertEqual("结束回合", parsed_calls[0][0].normalized_text)
        self.assertEqual(0.8, observation.confidence)
        self.assertTrue(observation.evidence_id.startswith("hsang:"))
        self.assertEqual(expected.snapshot_id, observation.snapshot_id)

    def test_evidence_id_is_deterministic_for_same_evidence(self) -> None:
        source, _captured, _parsed, _roi = self._source()
        first = source.recommend(snapshot(), RecommendationMode.TURN)
        second = source.recommend(snapshot(), RecommendationMode.TURN)
        self.assertEqual(first.evidence_id, second.evidence_id)

    def test_typed_failure_when_stable_reader_has_no_evidence(self) -> None:
        class RejectingReader:
            def read(self, frame_supplier, roi_supplier):
                del frame_supplier, roi_supplier
                raise RuntimeError("recommendation_not_stable")

        source = StableHsangRecommendationSource(
            lambda: SimpleNamespace(frame_id="f", exact_hash="h"),
            RejectingReader(),
            lambda ocr, turn, revision: None,
            mulligan_roi_supplier=lambda frame: frame,
            turn_roi_supplier=lambda frame: frame,
        )

        with self.assertRaises(HsangReadError) as raised:
            source.recommend(snapshot(), RecommendationMode.MULLIGAN)
        self.assertEqual("ocr_not_stable", raised.exception.reason_code)

    def test_mode_mismatch_is_rejected_without_guessing(self) -> None:
        source, _captured, _parsed, _roi = self._source(parsed_instruction="结束回合")
        with self.assertRaises(HsangObservationError) as raised:
            source.recommend(snapshot(), RecommendationMode.MULLIGAN)
        self.assertEqual("mode_mismatch", raised.exception.reason_code)


if __name__ == "__main__":
    unittest.main()
