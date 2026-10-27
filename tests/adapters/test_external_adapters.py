from __future__ import annotations

import time
import unittest
from types import SimpleNamespace

from hslegend.adapters.deadline import run_with_deadline
from hslegend.adapters.hsang import (
    HsangAdapterError,
    HsangCaptureError,
    HsangObservationError,
    HsangRecommendationAdapter,
)
from hslegend.adapters.input import LegacyActionDispatch, LegacyActionDispatchError
from hslegend.adapters.lobby import LegacyLobbyStateAdapter, LobbyStateAdapterError
from hslegend.adapters.powerlog import (
    PowerLogGameStateAdapter,
    PowerLogReadError,
    PowerLogValidationError,
)
from hslegend.application import (
    ActionDispatchPort,
    DispatchResult,
    DispatchStatus,
    GameStateSourcePort,
    LobbyPhase,
    LobbyState,
    LobbyStatePort,
    RecommendationMode,
    RecommendationObservation,
    RecommendationSourcePort,
)
from hslegend.domain import ActionIntent, PlanStep
from tests.domain.support import snapshot


def plan_step(action_type: str = "play_card") -> PlanStep:
    return PlanStep(
        step_id="step-1",
        intent=ActionIntent(action_type=action_type, intent_id="intent-1"),
    )


class FakeClock:
    def __init__(self, now: float = 10.0) -> None:
        self.now = now

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeStopToken:
    def __init__(self, cancelled: bool) -> None:
        self.cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self.cancelled


class HsangRecommendationAdapterTests(unittest.TestCase):
    def test_capture_read_parse_becomes_typed_observation(self) -> None:
        calls: list[tuple[str, object]] = []
        frame = SimpleNamespace(frame_id="frame-1")
        ocr = SimpleNamespace(frame_id="ocr-frame-1", normalized_text="打出1号位随从")

        def capture():
            calls.append(("capture", None))
            return frame

        def read(value):
            calls.append(("read", value))
            return ocr

        def parse(value, turn_number, log_revision):
            calls.append(("parse", (value, turn_number, log_revision)))
            return SimpleNamespace(
                frame_id="recommendation-1",
                turn_number=turn_number,
                log_revision=log_revision,
                normalized_instruction="打出1号位随从",
                raw_instruction="打出1号位随从",
                ocr_confidence=0.91,
                semantic_confidence=0.88,
            )

        adapter = HsangRecommendationAdapter(capture, read, parse)
        observation = adapter.recommend(snapshot(), RecommendationMode.TURN)

        self.assertEqual(RecommendationMode.TURN, observation.mode)
        self.assertEqual("打出1号位随从", observation.instruction)
        self.assertEqual(0.88, observation.confidence)
        self.assertEqual("recommendation-1", observation.evidence_id)
        self.assertEqual("snap-1", observation.snapshot_id)
        self.assertEqual(
            [
                ("capture", None),
                ("read", frame),
                ("parse", (ocr, 5, 7)),
            ],
            calls,
        )

    def test_invalid_confidence_is_a_typed_adapter_error(self) -> None:
        adapter = HsangRecommendationAdapter(
            lambda: object(),
            lambda frame: object(),
            lambda ocr, turn, revision: SimpleNamespace(
                normalized_instruction="保留全部卡牌",
                frame_id="frame-1",
                confidence=1.5,
            ),
        )

        with self.assertRaises(HsangObservationError) as raised:
            adapter.recommend(snapshot(), RecommendationMode.MULLIGAN)

        self.assertEqual("invalid_confidence", raised.exception.reason_code)

    def test_read_confidence_is_used_when_parser_omits_it(self) -> None:
        adapter = HsangRecommendationAdapter(
            lambda: SimpleNamespace(frame_id="frame-1"),
            lambda frame: SimpleNamespace(confidence=0.64),
            lambda ocr, turn, revision: {"instruction": "结束回合"},
        )

        observation = adapter.recommend(snapshot(), RecommendationMode.TURN)

        self.assertEqual(0.64, observation.confidence)

    def test_stale_parse_revision_is_rejected(self) -> None:
        adapter = HsangRecommendationAdapter(
            lambda: object(),
            lambda frame: object(),
            lambda ocr, turn, revision: SimpleNamespace(
                normalized_instruction="保留全部卡牌",
                frame_id="frame-1",
                confidence=0.9,
                turn_number=turn,
                log_revision=revision - 1,
            ),
        )

        with self.assertRaises(HsangAdapterError) as raised:
            adapter.recommend(snapshot(), RecommendationMode.MULLIGAN)

        self.assertEqual("stale_revision", raised.exception.reason_code)

    def test_parsed_observation_is_revalidated_before_return(self) -> None:
        def observation(**changes):
            value = RecommendationObservation(
                mode=RecommendationMode.TURN,
                instruction="结束回合",
                confidence=0.9,
                evidence_id="frame-1",
                snapshot_id="snap-1",
            )
            for name, replacement in changes.items():
                object.__setattr__(value, name, replacement)
            return value

        cases = (
            ({"instruction": "   "}, "invalid_observation"),
            ({"evidence_id": ""}, "invalid_observation"),
            ({"snapshot_id": ""}, "invalid_observation"),
            ({"snapshot_id": "snap-other"}, "snapshot_mismatch"),
            ({"mode": RecommendationMode.MULLIGAN}, "mode_mismatch"),
            ({"mode": "turn"}, "invalid_mode"),
            ({"confidence": 1.5}, "invalid_confidence"),
        )
        for changes, reason_code in cases:
            with self.subTest(changes=changes):
                parsed = observation(**changes)
                adapter = HsangRecommendationAdapter(
                    lambda: object(),
                    lambda frame: object(),
                    lambda ocr, turn, revision, parsed=parsed: parsed,
                )

                with self.assertRaises(HsangObservationError) as raised:
                    adapter.recommend(snapshot(), RecommendationMode.TURN)

                self.assertEqual(reason_code, raised.exception.reason_code)

    def test_capture_failure_keeps_the_legacy_cause_typed(self) -> None:
        def capture():
            raise RuntimeError("desktop unavailable")

        adapter = HsangRecommendationAdapter(capture, lambda frame: frame, lambda *args: None)

        with self.assertRaises(HsangCaptureError) as raised:
            adapter.recommend(snapshot(), RecommendationMode.TURN)

        self.assertEqual("capture_failed", raised.exception.reason_code)
        self.assertIsInstance(raised.exception.__cause__, RuntimeError)


class PowerLogGameStateAdapterTests(unittest.TestCase):
    def test_current_wraps_snapshot_with_injected_confidence(self) -> None:
        adapter = PowerLogGameStateAdapter(
            lambda: snapshot(revision=11, snapshot_id="snap-11"),
            lambda: 11,
            confidence_provider=lambda: 0.73,
        )

        observation = adapter.current()

        self.assertEqual(11, observation.snapshot.log_revision)
        self.assertEqual(0.73, observation.source_confidence)

    def test_wait_returns_later_observation(self) -> None:
        clock = FakeClock()
        revisions = iter((7, 7, 8))
        adapter = PowerLogGameStateAdapter(
            lambda: snapshot(revision=8, snapshot_id="snap-8"),
            lambda: next(revisions),
            clock=clock.time,
            sleep_func=clock.sleep,
            poll_interval_seconds=0.25,
        )

        observation = adapter.wait_for_revision_change(7, 1.0)

        self.assertIsNotNone(observation)
        assert observation is not None
        self.assertEqual(8, observation.snapshot.log_revision)
        self.assertEqual(10.5, clock.now)

    def test_wait_returns_none_after_timeout(self) -> None:
        clock = FakeClock()
        adapter = PowerLogGameStateAdapter(
            lambda: snapshot(revision=7),
            lambda: 7,
            clock=clock.time,
            sleep_func=clock.sleep,
            poll_interval_seconds=0.25,
        )

        observation = adapter.wait_for_revision_change(7, 0.5)

        self.assertIsNone(observation)
        self.assertEqual(10.5, clock.now)

    def test_wait_honors_stop_token_without_reading_providers(self) -> None:
        calls = 0

        def revision_provider():
            nonlocal calls
            calls += 1
            return 7

        adapter = PowerLogGameStateAdapter(
            lambda: snapshot(),
            revision_provider,
            stop_token=FakeStopToken(True),
        )

        self.assertIsNone(adapter.wait_for_revision_change(7, 1.0))
        self.assertEqual(0, calls)

    def test_slow_revision_read_counts_against_timeout_from_entry(self) -> None:
        clock = FakeClock()
        sleeps: list[float] = []

        def revision_provider():
            clock.now += 0.75
            return 7

        adapter = PowerLogGameStateAdapter(
            lambda: snapshot(),
            revision_provider,
            clock=clock.time,
            sleep_func=sleeps.append,
            poll_interval_seconds=0.25,
        )

        self.assertIsNone(adapter.wait_for_revision_change(7, 0.5))
        self.assertEqual([], sleeps)

    def test_mid_read_cancellation_suppresses_a_new_revision(self) -> None:
        stop_token = FakeStopToken(False)
        snapshot_calls: list[None] = []

        def revision_provider():
            stop_token.cancelled = True
            return 8

        def snapshot_provider():
            snapshot_calls.append(None)
            return snapshot(revision=8)

        adapter = PowerLogGameStateAdapter(
            snapshot_provider,
            revision_provider,
            stop_token=stop_token,
        )

        self.assertIsNone(adapter.wait_for_revision_change(7, 1.0))
        self.assertEqual([], snapshot_calls)

    def test_cancellation_during_current_suppresses_the_observation(self) -> None:
        stop_token = FakeStopToken(False)

        def snapshot_provider():
            stop_token.cancelled = True
            return snapshot(revision=8)

        adapter = PowerLogGameStateAdapter(
            snapshot_provider,
            lambda: 8,
            stop_token=stop_token,
        )

        self.assertIsNone(adapter.wait_for_revision_change(7, 1.0))

    def test_slow_current_read_cannot_return_after_deadline(self) -> None:
        clock = FakeClock()

        def snapshot_provider():
            clock.now += 0.75
            return snapshot(revision=8)

        adapter = PowerLogGameStateAdapter(
            snapshot_provider,
            lambda: 8,
            clock=clock.time,
            sleep_func=clock.sleep,
            poll_interval_seconds=0.25,
        )

        self.assertIsNone(adapter.wait_for_revision_change(7, 0.5))

    def test_snapshot_failure_is_typed(self) -> None:
        def snapshot_provider():
            raise OSError("Power.log missing")

        adapter = PowerLogGameStateAdapter(snapshot_provider, lambda: 1)

        with self.assertRaises(PowerLogReadError) as raised:
            adapter.current()

        self.assertEqual("snapshot_failed", raised.exception.reason_code)

    def test_negative_revision_is_rejected(self) -> None:
        adapter = PowerLogGameStateAdapter(lambda: snapshot(revision=-1), lambda: 0)

        with self.assertRaises(PowerLogValidationError) as raised:
            adapter.current()

        self.assertEqual("invalid_log_revision", raised.exception.reason_code)


class AdapterDeadlineTests(unittest.TestCase):
    def test_worker_exception_propagates(self) -> None:
        def callback():
            raise ValueError("worker failed")

        with self.assertRaisesRegex(ValueError, "worker failed"):
            run_with_deadline(callback, timeout_seconds=0.25)


class LegacyLobbyStateAdapterTests(unittest.TestCase):
    def test_known_aliases_map_deterministically(self) -> None:
        expected = {
            "main_menu": LobbyPhase.MAIN_MENU,
            "deck_select": LobbyPhase.DECK_SELECT,
            "matching": LobbyPhase.MATCHING,
            "in_game": LobbyPhase.IN_GAME,
            "result": LobbyPhase.RESULT,
        }
        for raw, phase in expected.items():
            with self.subTest(raw=raw):
                adapter = LegacyLobbyStateAdapter(
                    lambda raw=raw: {"state": raw, "confidence": 0.5, "evidence_id": "frame-1"},
                    clock=lambda: 12.0,
                )
                state = adapter.get_state(timeout_seconds=1.0)
                self.assertEqual(phase, state.phase)
                self.assertEqual(0.5, state.confidence)
                self.assertEqual("frame-1", state.evidence_id)
                self.assertEqual(12.0, state.observed_at)

    def test_unknown_state_fails_closed_to_unknown(self) -> None:
        adapter = LegacyLobbyStateAdapter(lambda: "new_unrecognized_screen", clock=lambda: 1.0)

        state = adapter.get_state(timeout_seconds=1.0)

        self.assertEqual(LobbyPhase.UNKNOWN, state.phase)
        self.assertEqual(0.0, state.confidence)

    def test_recognizer_failure_is_typed(self) -> None:
        def recognizer():
            raise OSError("capture missing")

        adapter = LegacyLobbyStateAdapter(recognizer)

        with self.assertRaises(LobbyStateAdapterError) as raised:
            adapter.get_state(timeout_seconds=1.0)

        self.assertEqual("recognition_failed", raised.exception.reason_code)

    def test_invalid_confidence_is_typed(self) -> None:
        adapter = LegacyLobbyStateAdapter(
            lambda: {"state": "main_menu", "confidence": float("nan")},
        )

        with self.assertRaises(LobbyStateAdapterError) as raised:
            adapter.get_state(timeout_seconds=1.0)

        self.assertEqual("invalid_confidence", raised.exception.reason_code)

    def test_timeout_budget_is_forwarded_and_timeout_error_propagates(self) -> None:
        budgets: list[float] = []

        def recognizer(*, timeout_seconds: float):
            budgets.append(timeout_seconds)
            raise TimeoutError("capture timed out")

        adapter = LegacyLobbyStateAdapter(recognizer)

        with self.assertRaises(TimeoutError):
            adapter.get_state(timeout_seconds=0.25)

        self.assertEqual([0.25], budgets)

    def test_slow_recognizer_returns_within_deadline_budget(self) -> None:
        def recognizer(*, timeout_seconds: float):
            time.sleep(0.3)
            return "main_menu"

        adapter = LegacyLobbyStateAdapter(recognizer)
        started_at = time.monotonic()

        with self.assertRaises(TimeoutError):
            adapter.get_state(timeout_seconds=0.05)

        self.assertLess(time.monotonic() - started_at, 0.15)


class LegacyActionDispatchTests(unittest.TestCase):
    def test_known_handler_receives_step_and_snapshot_then_reports_sent(self) -> None:
        calls: list[tuple[PlanStep, object]] = []
        current_snapshot = snapshot()
        step = plan_step()

        def handler(received_step, received_snapshot):
            calls.append((received_step, received_snapshot))
            return None

        adapter = LegacyActionDispatch(
            {"play_card": handler},
            dispatch_id_factory=lambda: "dispatch-1",
        )

        result = adapter.dispatch(step, current_snapshot, timeout_seconds=1.0)

        self.assertEqual(DispatchStatus.SENT, result.status)
        self.assertEqual("dispatch-1", result.dispatch_id)
        self.assertEqual([(step, current_snapshot)], calls)

    def test_unknown_action_is_rejected_before_handler_lookup(self) -> None:
        calls: list[str] = []

        def handler(step, current_snapshot):
            calls.append(step.intent.action_type)
            return DispatchResult.sent("must-not-run")

        adapter = LegacyActionDispatch({"play_card": handler})

        result = adapter.dispatch(plan_step("unknown_action"), snapshot(), timeout_seconds=1.0)

        self.assertEqual(DispatchStatus.REJECTED, result.status)
        self.assertEqual("unknown_action", result.reason_code)
        self.assertEqual([], calls)

    def test_handler_exception_becomes_failed_dispatch_result(self) -> None:
        def handler(step, current_snapshot):
            raise RuntimeError("click backend unavailable")

        adapter = LegacyActionDispatch({"play_card": handler})

        result = adapter.dispatch(plan_step(), snapshot(), timeout_seconds=1.0)

        self.assertEqual(DispatchStatus.FAILED, result.status)
        self.assertEqual("handler_exception", result.reason_code)

    def test_mapping_result_is_normalized(self) -> None:
        adapter = LegacyActionDispatch(
            {
                "END-TURN": lambda step, current_snapshot: {
                    "status": "sent",
                    "evidence_ids": ["turn-1"],
                }
            },
            dispatch_id_factory=lambda: "dispatch-2",
        )

        result = adapter.dispatch(plan_step("end_turn"), snapshot(), timeout_seconds=1.0)

        self.assertEqual(DispatchStatus.SENT, result.status)
        self.assertEqual("dispatch-2", result.dispatch_id)
        self.assertEqual(("turn-1",), result.evidence_ids)

    def test_rejected_result_does_not_request_a_dispatch_id(self) -> None:
        def factory():
            raise AssertionError("dispatch id must not be requested")

        adapter = LegacyActionDispatch(
            {"play_card": lambda step, current: DispatchResult.rejected("not_ready")},
            dispatch_id_factory=factory,
        )

        result = adapter.dispatch(plan_step(), snapshot(), timeout_seconds=1.0)

        self.assertEqual(DispatchStatus.REJECTED, result.status)
        self.assertEqual("not_ready", result.reason_code)

    def test_malformed_handler_result_is_typed(self) -> None:
        adapter = LegacyActionDispatch(
            {"play_card": lambda step, current_snapshot: {"status": "unknown"}},
        )

        with self.assertRaises(LegacyActionDispatchError) as raised:
            adapter.dispatch(plan_step(), snapshot(), timeout_seconds=1.0)

        self.assertEqual("invalid_handler_result", raised.exception.reason_code)

    def test_duplicate_normalized_handler_names_are_rejected(self) -> None:
        with self.assertRaises(LegacyActionDispatchError) as raised:
            LegacyActionDispatch(
                {
                    "play_card": lambda step, current_snapshot: None,
                    "PLAY-CARD": lambda step, current_snapshot: None,
                }
            )

        self.assertEqual("duplicate_action_type", raised.exception.reason_code)

    def test_timeout_budget_is_forwarded_and_timeout_error_propagates(self) -> None:
        budgets: list[float] = []

        def handler(step, current_snapshot, *, timeout_seconds: float):
            budgets.append(timeout_seconds)
            raise TimeoutError("input timed out")

        adapter = LegacyActionDispatch({"play_card": handler})

        with self.assertRaises(TimeoutError):
            adapter.dispatch(plan_step(), snapshot(), timeout_seconds=0.25)

        self.assertEqual([0.25], budgets)

    def test_slow_handler_returns_within_deadline_budget(self) -> None:
        def handler(step, current_snapshot, *, timeout_seconds: float):
            time.sleep(0.3)
            return None

        adapter = LegacyActionDispatch({"play_card": handler})
        started_at = time.monotonic()

        with self.assertRaises(TimeoutError):
            adapter.dispatch(plan_step(), snapshot(), timeout_seconds=0.05)

        self.assertLess(time.monotonic() - started_at, 0.15)


class ExternalAdapterPortConformanceTests(unittest.TestCase):
    def test_adapters_satisfy_application_ports(self) -> None:
        hsang = HsangRecommendationAdapter(
            lambda: object(),
            lambda frame: object(),
            lambda ocr, turn, revision: SimpleNamespace(
                normalized_instruction="结束回合",
                frame_id="frame-1",
                confidence=1.0,
            ),
        )
        powerlog = PowerLogGameStateAdapter(lambda: snapshot(), lambda: 7)
        lobby = LegacyLobbyStateAdapter(lambda: "main_menu")
        dispatch = LegacyActionDispatch({"end_turn": lambda step, current: None})

        for port, adapter in (
            (RecommendationSourcePort, hsang),
            (GameStateSourcePort, powerlog),
            (LobbyStatePort, lobby),
            (ActionDispatchPort, dispatch),
        ):
            with self.subTest(port=port.__name__):
                self.assertIsInstance(adapter, port)


if __name__ == "__main__":
    unittest.main()
