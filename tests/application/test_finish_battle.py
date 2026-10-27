from __future__ import annotations

import dataclasses
import unittest

from hslegend.application.dto import LobbyPhase, LobbyState
from hslegend.application.runtime import StopToken
from hslegend.application.use_cases.finish_battle import (
    FinishBattleCommand,
    FinishBattleResult,
    FinishBattleStatus,
    FinishBattleUseCase,
)
from hslegend.bootstrap import FinishBattleCommand as BootstrapFinishBattleCommand
from hslegend.bootstrap import build_finish_battle_use_case


def lobby_state(
    phase: LobbyPhase,
    *,
    observed_at: float = 0.0,
    evidence_id: str = "",
    confidence: float = 1.0,
) -> LobbyState:
    return LobbyState(
        phase=phase,
        observed_at=observed_at,
        confidence=confidence,
        evidence_id=evidence_id,
    )


class FakeClock:
    def __init__(self, now: float = 0.0, *, fail_on_call: int | None = None) -> None:
        self.now = now
        self.fail_on_call = fail_on_call
        self.calls = 0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        self.calls += 1
        if self.fail_on_call == self.calls:
            raise RuntimeError("clock unavailable")
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FailingClock:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        raise RuntimeError("clock unavailable")


class ScriptedLobby:
    def __init__(
        self,
        observations: list[object],
        *,
        on_get: object | None = None,
    ) -> None:
        if not observations:
            raise ValueError("at least one observation is required")
        self._observations = list(observations)
        self._on_get = on_get
        self.timeout_calls: list[float] = []
        self.calls = 0

    def get_state(self, *, timeout_seconds: float) -> LobbyState:
        self.timeout_calls.append(timeout_seconds)
        index = min(self.calls, len(self._observations) - 1)
        self.calls += 1
        observation = self._observations[index]
        if self._on_get is not None:
            self._on_get(self.calls, observation)  # type: ignore[operator]
        if isinstance(observation, BaseException):
            raise observation
        return observation  # type: ignore[return-value]


class FinishBattleUseCaseTests(unittest.TestCase):
    def make_use_case(
        self,
        observations: list[object],
        *,
        timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.25,
        token: StopToken | None = None,
        clock: object | None = None,
        on_get: object | None = None,
        sleep: object | None = None,
    ) -> tuple[FinishBattleUseCase, ScriptedLobby, StopToken, object]:
        token = token or StopToken()
        clock = clock or FakeClock()
        lobby = ScriptedLobby(observations, on_get=on_get)
        use_case = FinishBattleUseCase(
            lobby=lobby,
            stop_token=token,
            clock=clock,
            sleep=sleep if callable(sleep) else None,
        )
        return use_case, lobby, token, clock

    def test_immediate_result_completes_without_sleeping(self) -> None:
        use_case, lobby, _, clock = self.make_use_case(
            [lobby_state(LobbyPhase.RESULT, evidence_id="result-frame")],
            timeout_seconds=1.5,
            poll_interval_seconds=0.2,
        )

        result = use_case.execute(
            FinishBattleCommand(
                command_id="finish-1",
                timeout_seconds=1.5,
                poll_interval_seconds=0.2,
            )
        )

        self.assertTrue(result.ok)
        self.assertIs(FinishBattleStatus.COMPLETED, result.status)
        self.assertEqual("completed", result.reason_code)
        self.assertEqual(LobbyPhase.RESULT, result.phase)
        self.assertEqual(("result-frame",), result.evidence_ids)
        self.assertEqual(0.0, result.elapsed_seconds)
        self.assertEqual([1.5], lobby.timeout_calls)
        self.assertEqual([], clock.sleeps)  # type: ignore[attr-defined]

    def test_delayed_result_deduplicates_evidence_and_reports_elapsed(self) -> None:
        use_case, lobby, _, clock = self.make_use_case(
            [
                lobby_state(LobbyPhase.IN_GAME, evidence_id="frame-1"),
                lobby_state(LobbyPhase.IN_GAME, evidence_id="frame-1"),
                lobby_state(LobbyPhase.RESULT, evidence_id="result-frame"),
            ],
            timeout_seconds=3.0,
            poll_interval_seconds=0.5,
        )

        result = use_case.execute(
            FinishBattleCommand(
                command_id="finish-delayed",
                timeout_seconds=3.0,
                poll_interval_seconds=0.5,
            )
        )

        self.assertIs(FinishBattleStatus.COMPLETED, result.status)
        self.assertEqual(LobbyPhase.RESULT, result.phase)
        self.assertEqual(("frame-1", "result-frame"), result.evidence_ids)
        self.assertEqual(1.0, result.elapsed_seconds)
        self.assertEqual([3.0, 2.5, 2.0], lobby.timeout_calls)
        self.assertEqual([0.5, 0.5], clock.sleeps)  # type: ignore[attr-defined]

    def test_timeout_is_bounded_by_total_deadline(self) -> None:
        use_case, lobby, _, clock = self.make_use_case(
            [lobby_state(LobbyPhase.IN_GAME, evidence_id="game-frame")],
            timeout_seconds=1.0,
            poll_interval_seconds=0.25,
        )

        result = use_case.execute(
            FinishBattleCommand(
                command_id="finish-timeout",
                timeout_seconds=1.0,
                poll_interval_seconds=0.25,
            )
        )

        self.assertIs(FinishBattleStatus.TIMED_OUT, result.status)
        self.assertEqual("timeout", result.reason_code)
        self.assertEqual(LobbyPhase.IN_GAME, result.phase)
        self.assertEqual(("game-frame",), result.evidence_ids)
        self.assertEqual(1.0, result.elapsed_seconds)
        self.assertEqual([1.0, 0.75, 0.5, 0.25], lobby.timeout_calls)
        self.assertEqual([0.25, 0.25, 0.25, 0.25], clock.sleeps)  # type: ignore[attr-defined]

    def test_cancellation_before_poll_is_terminal_without_reads(self) -> None:
        token = StopToken()
        token.cancel("operator-stop")
        use_case, lobby, _, _ = self.make_use_case(
            [lobby_state(LobbyPhase.RESULT, evidence_id="result-frame")],
            token=token,
        )

        result = use_case.execute(
            FinishBattleCommand("finish-cancelled", 5.0, 0.25)
        )

        self.assertIs(FinishBattleStatus.CANCELLED, result.status)
        self.assertEqual("cancelled", result.reason_code)
        self.assertEqual(LobbyPhase.UNKNOWN, result.phase)
        self.assertEqual([], lobby.timeout_calls)

    def test_cancellation_before_poll_reports_zero_elapsed_without_clock_read(
        self,
    ) -> None:
        token = StopToken()
        token.cancel("operator-stop")
        clock = FakeClock(now=100.0)
        use_case, lobby, _, _ = self.make_use_case(
            [lobby_state(LobbyPhase.RESULT, evidence_id="result-frame")],
            token=token,
            clock=clock,
        )

        result = use_case.execute(
            FinishBattleCommand("finish-cancelled-before-start", 5.0, 0.25)
        )

        self.assertIs(FinishBattleStatus.CANCELLED, result.status)
        self.assertEqual("cancelled", result.reason_code)
        self.assertEqual(LobbyPhase.UNKNOWN, result.phase)
        self.assertEqual(0.0, result.elapsed_seconds)
        self.assertEqual(0, clock.calls)
        self.assertEqual([], lobby.timeout_calls)

    def test_cancellation_after_bounded_read_preserves_observation(self) -> None:
        token = StopToken()

        def cancel_on_get(_index: int, _observation: object) -> None:
            token.cancel("operator-stop")

        use_case, lobby, _, clock = self.make_use_case(
            [lobby_state(LobbyPhase.IN_GAME, evidence_id="game-frame")],
            token=token,
            on_get=cancel_on_get,
        )

        result = use_case.execute(
            FinishBattleCommand("finish-cancel-read", 5.0, 0.25)
        )

        self.assertIs(FinishBattleStatus.CANCELLED, result.status)
        self.assertEqual(LobbyPhase.IN_GAME, result.phase)
        self.assertEqual(("game-frame",), result.evidence_ids)
        self.assertEqual(1, len(lobby.timeout_calls))
        self.assertEqual([], clock.sleeps)  # type: ignore[attr-defined]

    def test_adapter_failure_is_typed_and_preserves_prior_evidence(self) -> None:
        use_case, lobby, _, _ = self.make_use_case(
            [
                lobby_state(LobbyPhase.IN_GAME, evidence_id="game-frame"),
                RuntimeError("adapter crashed"),
            ]
        )

        result = use_case.execute(
            FinishBattleCommand("finish-adapter", 5.0, 0.25)
        )

        self.assertIs(FinishBattleStatus.FAILED, result.status)
        self.assertEqual("lobby_state_error", result.reason_code)
        self.assertEqual(LobbyPhase.IN_GAME, result.phase)
        self.assertEqual(("game-frame",), result.evidence_ids)
        self.assertEqual(2, len(lobby.timeout_calls))

    def test_adapter_timeout_before_deadline_is_typed_failure(self) -> None:
        use_case, lobby, _, _ = self.make_use_case(
            [TimeoutError("bounded read expired")]
        )

        result = use_case.execute(
            FinishBattleCommand("finish-read-timeout", 5.0, 0.25)
        )

        self.assertIs(FinishBattleStatus.FAILED, result.status)
        self.assertEqual("lobby_state_timeout", result.reason_code)
        self.assertEqual(LobbyPhase.UNKNOWN, result.phase)
        self.assertEqual([5.0], lobby.timeout_calls)

    def test_adapter_timeout_at_deadline_is_typed_timed_out(self) -> None:
        clock = FakeClock()

        def reach_deadline(_index: int, _observation: object) -> None:
            clock.now = 5.0

        use_case, lobby, _, _ = self.make_use_case(
            [TimeoutError("bounded read expired")],
            clock=clock,
            on_get=reach_deadline,
        )
        result = use_case.execute(
            FinishBattleCommand("finish-read-timeout", 5.0, 0.25)
        )

        self.assertIs(FinishBattleStatus.TIMED_OUT, result.status)
        self.assertEqual("timeout", result.reason_code)
        self.assertEqual(5.0, result.elapsed_seconds)
        self.assertEqual([5.0], lobby.timeout_calls)

    def test_cancellation_masks_adapter_timeout_failure(self) -> None:
        token = StopToken()

        def cancel_on_second_get(index: int, _observation: object) -> None:
            if index == 2:
                token.cancel("operator-stop")

        use_case, lobby, _, _ = self.make_use_case(
            [
                lobby_state(LobbyPhase.IN_GAME, evidence_id="game-frame"),
                TimeoutError("bounded read expired"),
            ],
            token=token,
            on_get=cancel_on_second_get,
        )

        result = use_case.execute(
            FinishBattleCommand("finish-cancel-timeout", 5.0, 0.25)
        )

        self.assertIs(FinishBattleStatus.CANCELLED, result.status)
        self.assertEqual("cancelled", result.reason_code)
        self.assertEqual(LobbyPhase.IN_GAME, result.phase)
        self.assertEqual(("game-frame",), result.evidence_ids)
        self.assertEqual(2, len(lobby.timeout_calls))

    def test_cancellation_masks_adapter_exception_failure(self) -> None:
        token = StopToken()

        def cancel_on_second_get(index: int, _observation: object) -> None:
            if index == 2:
                token.cancel("operator-stop")

        use_case, lobby, _, _ = self.make_use_case(
            [
                lobby_state(LobbyPhase.IN_GAME, evidence_id="game-frame"),
                RuntimeError("adapter crashed"),
            ],
            token=token,
            on_get=cancel_on_second_get,
        )

        result = use_case.execute(
            FinishBattleCommand("finish-cancel-error", 5.0, 0.25)
        )

        self.assertIs(FinishBattleStatus.CANCELLED, result.status)
        self.assertEqual("cancelled", result.reason_code)
        self.assertEqual(LobbyPhase.IN_GAME, result.phase)
        self.assertEqual(("game-frame",), result.evidence_ids)
        self.assertEqual(2, len(lobby.timeout_calls))

    def test_cancellation_masks_invalid_state_failure(self) -> None:
        token = StopToken()

        def cancel_on_second_get(index: int, _observation: object) -> None:
            if index == 2:
                token.cancel("operator-stop")

        use_case, lobby, _, _ = self.make_use_case(
            [
                lobby_state(LobbyPhase.IN_GAME, evidence_id="game-frame"),
                object(),
            ],
            token=token,
            on_get=cancel_on_second_get,
        )

        result = use_case.execute(
            FinishBattleCommand("finish-cancel-invalid", 5.0, 0.25)
        )

        self.assertIs(FinishBattleStatus.CANCELLED, result.status)
        self.assertEqual("cancelled", result.reason_code)
        self.assertEqual(LobbyPhase.IN_GAME, result.phase)
        self.assertEqual(("game-frame",), result.evidence_ids)
        self.assertEqual(2, len(lobby.timeout_calls))

    def test_sleeper_failure_is_typed_and_preserves_observation(self) -> None:
        def failing_sleep(_seconds: float) -> None:
            raise RuntimeError("sleeper failed")

        use_case, lobby, _, _ = self.make_use_case(
            [lobby_state(LobbyPhase.IN_GAME, evidence_id="game-frame")],
            sleep=failing_sleep,
        )

        result = use_case.execute(
            FinishBattleCommand("finish-sleep-error", 5.0, 0.25)
        )

        self.assertIs(FinishBattleStatus.FAILED, result.status)
        self.assertEqual("sleep_error", result.reason_code)
        self.assertEqual(LobbyPhase.IN_GAME, result.phase)
        self.assertEqual(("game-frame",), result.evidence_ids)
        self.assertEqual(1, len(lobby.timeout_calls))

    def test_cancellation_masks_sleeper_failure(self) -> None:
        token = StopToken()

        def cancel_then_fail(_seconds: float) -> None:
            token.cancel("operator-stop")
            raise RuntimeError("sleeper failed")

        use_case, lobby, _, _ = self.make_use_case(
            [lobby_state(LobbyPhase.IN_GAME, evidence_id="game-frame")],
            token=token,
            sleep=cancel_then_fail,
        )

        result = use_case.execute(
            FinishBattleCommand("finish-cancel-sleep", 5.0, 0.25)
        )

        self.assertIs(FinishBattleStatus.CANCELLED, result.status)
        self.assertEqual("cancelled", result.reason_code)
        self.assertEqual(LobbyPhase.IN_GAME, result.phase)
        self.assertEqual(("game-frame",), result.evidence_ids)
        self.assertEqual(1, len(lobby.timeout_calls))

    def test_clock_failure_is_typed_without_reading_lobby(self) -> None:
        clock = FailingClock()
        use_case, lobby, _, _ = self.make_use_case(
            [lobby_state(LobbyPhase.RESULT)],
            clock=clock,
        )

        result = use_case.execute(
            FinishBattleCommand("finish-clock", 5.0, 0.25)
        )

        self.assertIs(FinishBattleStatus.FAILED, result.status)
        self.assertEqual("clock_error", result.reason_code)
        self.assertEqual([], lobby.timeout_calls)
        self.assertEqual(1, clock.calls)

    def test_clock_failure_after_read_fails_closed_and_preserves_result_evidence(self) -> None:
        clock = FakeClock(fail_on_call=3)
        use_case, lobby, _, _ = self.make_use_case(
            [lobby_state(LobbyPhase.RESULT, evidence_id="result-frame")],
            clock=clock,
        )

        result = use_case.execute(
            FinishBattleCommand("finish-clock-after-read", 5.0, 0.25)
        )

        self.assertIs(FinishBattleStatus.FAILED, result.status)
        self.assertEqual("clock_error", result.reason_code)
        self.assertEqual(LobbyPhase.RESULT, result.phase)
        self.assertEqual(("result-frame",), result.evidence_ids)
        self.assertEqual(1, len(lobby.timeout_calls))

    def test_invalid_state_is_typed_failure(self) -> None:
        use_case, lobby, _, _ = self.make_use_case([object()])

        result = use_case.execute(
            FinishBattleCommand("finish-invalid-state", 5.0, 0.25)
        )

        self.assertIs(FinishBattleStatus.FAILED, result.status)
        self.assertEqual("lobby_state_invalid", result.reason_code)
        self.assertEqual(1, len(lobby.timeout_calls))

    def test_command_and_result_are_immutable_and_validate_inputs(self) -> None:
        command = FinishBattleCommand("finish-contract", 2.0, 0.1)
        result = FinishBattleResult(
            command_id=command.command_id,
            status=FinishBattleStatus.COMPLETED,
            phase=LobbyPhase.RESULT,
            reason_code="completed",
            evidence_ids=("evidence-1", "evidence-1"),
            elapsed_seconds=0.5,
        )

        self.assertTrue(dataclasses.is_dataclass(command))
        self.assertTrue(command.__dataclass_params__.frozen)
        self.assertIsNotNone(command.__slots__)
        self.assertTrue(dataclasses.is_dataclass(result))
        self.assertTrue(result.__dataclass_params__.frozen)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            command.timeout_seconds = 3.0  # type: ignore[misc]
        with self.assertRaises(ValueError):
            FinishBattleCommand("", 2.0, 0.1)
        with self.assertRaises(ValueError):
            FinishBattleCommand("finish-contract", 0.0, 0.1)
        with self.assertRaises(ValueError):
            FinishBattleCommand("finish-contract", 2.0, 0.0)

    def test_bootstrap_builder_exports_observation_only_use_case(self) -> None:
        clock = FakeClock()
        lobby = ScriptedLobby(
            [lobby_state(LobbyPhase.RESULT, evidence_id="result-frame")]
        )

        use_case = build_finish_battle_use_case(
            lobby,
            stop_token=StopToken(),
            clock=clock,
        )
        result = use_case.execute(
            BootstrapFinishBattleCommand("finish-bootstrap", 5.0, 0.25)
        )

        self.assertIsInstance(use_case, FinishBattleUseCase)
        self.assertIs(FinishBattleStatus.COMPLETED, result.status)
        self.assertEqual([], clock.sleeps)

if __name__ == "__main__":
    unittest.main()
