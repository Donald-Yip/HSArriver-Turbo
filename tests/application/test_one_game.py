from __future__ import annotations

import unittest
from dataclasses import replace

from hslegend.application import (
    GameObservation,
    LobbyPhase,
    LobbyState,
    OneGameCommand,
    OneGameResult,
    OneGameStatus,
    OneGameUseCase,
)
from hslegend.application.use_cases.enter_battle import (
    EnterBattleResult,
    EnterBattleStatus,
    EnterBattleUseCase,
)
from hslegend.application.use_cases.finish_battle import (
    FinishBattleResult,
    FinishBattleStatus,
    FinishBattleUseCase,
)
from hslegend.application.use_cases.mulligan import (
    MulliganResult,
    MulliganStatus,
    MulliganUseCase,
)
from hslegend.application.runtime import StopToken
from hslegend.application.use_cases.turn import (
    PlayTurnResult,
    PlayTurnStatus,
    PlayTurnUseCase,
)
from hslegend.domain import EntityRef, GameSnapshot, PlayerView


def turn_snapshot() -> GameSnapshot:
    return GameSnapshot(
        schema_version=1,
        snapshot_id="turn-snapshot",
        log_revision=2,
        game_generation=7,
        observed_at=2.0,
        turn_number=1,
        is_my_turn=True,
        is_end=False,
        my_player=PlayerView(
            player_id=10,
            side="friendly",
            hero=EntityRef(1, "hero-friendly", "friendly", "hero", 0),
        ),
        opponent=PlayerView(
            player_id=20,
            side="opponent",
            hero=EntityRef(2, "hero-opponent", "opponent", "hero", 0),
        ),
    )


class StubEnterBattle(EnterBattleUseCase):
    def __init__(self, result: EnterBattleResult) -> None:
        self.result = result
        self.calls = 0

    def execute(self, command):  # type: ignore[override]
        self.calls += 1
        return self.result


class StubMulligan(MulliganUseCase):
    def __init__(self, result: MulliganResult) -> None:
        self.result = result
        self.calls = 0

    def execute(self, command):  # type: ignore[override]
        self.calls += 1
        return self.result


class StubTurn(PlayTurnUseCase):
    def __init__(self, result: PlayTurnResult) -> None:
        self.result = result
        self.calls = 0

    def execute(self, command):  # type: ignore[override]
        self.calls += 1
        return self.result


class StubFinish(FinishBattleUseCase):
    def __init__(self, result: FinishBattleResult) -> None:
        self.result = result
        self.calls = 0

    def execute(self, command):  # type: ignore[override]
        self.calls += 1
        return self.result


class StaticLobby:
    def __init__(self, phase: LobbyPhase) -> None:
        self.phase = phase
        self.calls = 0

    def get_state(self, *, timeout_seconds: float) -> LobbyState:
        self.calls += 1
        return LobbyState(self.phase, float(self.calls), 1.0, "lobby-evidence")


class TimeoutLobby:
    def get_state(self, *, timeout_seconds: float) -> LobbyState:
        raise TimeoutError("lobby timed out early")


class CancellingLobby:
    def __init__(self, token: StopToken) -> None:
        self._token = token

    def get_state(self, *, timeout_seconds: float) -> LobbyState:
        self._token.cancel("lobby_cancelled")
        raise RuntimeError("lobby failed with cancellation racing")


class StaticState:
    def __init__(self) -> None:
        self.observation = GameObservation(turn_snapshot(), 1.0)

    def current(self) -> GameObservation:
        return self.observation

    def wait_for_revision_change(self, after_revision: int, timeout_seconds: float):
        return None


class AdvancingState(StaticState):
    def __init__(self) -> None:
        super().__init__()
        self.after = GameObservation(
            replace(
                turn_snapshot(),
                snapshot_id="result-snapshot",
                log_revision=3,
                is_my_turn=False,
                is_end=True,
            ),
            1.0,
        )
        self.wait_calls = 0

    def wait_for_revision_change(self, after_revision: int, timeout_seconds: float):
        self.wait_calls += 1
        return self.after


class SequenceState:
    def __init__(self, observations: list[GameObservation]) -> None:
        if not observations:
            raise ValueError("at least one observation is required")
        self._observations = list(observations)
        self._index = 0
        self.wait_calls = 0

    def current(self) -> GameObservation:
        return self._observations[self._index]

    def wait_for_revision_change(self, after_revision: int, timeout_seconds: float):
        self.wait_calls += 1
        if self._index + 1 >= len(self._observations):
            return None
        candidate = self._observations[self._index + 1]
        if candidate.snapshot.log_revision <= after_revision:
            return self.current()
        self._index += 1
        return candidate


class CancelOnWaitState(StaticState):
    def __init__(self, token: StopToken) -> None:
        super().__init__()
        self._token = token
        self.wait_calls = 0

    def wait_for_revision_change(self, after_revision: int, timeout_seconds: float):
        self.wait_calls += 1
        self._token.cancel("wait_cancelled")
        return None


class SequenceClock:
    def __init__(self, values: list[float]) -> None:
        if not values:
            raise ValueError("at least one clock value is required")
        self._values = list(values)
        self.calls = 0

    def __call__(self) -> float:
        value = self._values[min(self.calls, len(self._values) - 1)]
        self.calls += 1
        return value


def make_use_case(
    *,
    enter: EnterBattleResult,
    mulligan: MulliganResult | None = None,
    turn: PlayTurnResult | None = None,
    finish: FinishBattleResult | None = None,
    state: StaticState | None = None,
    lobby: object | None = None,
    token: StopToken | None = None,
    clock=None,
    sleep=None,
) -> tuple[OneGameUseCase, StubEnterBattle, StubMulligan, StubTurn, StubFinish]:
    enter_stub = StubEnterBattle(enter)
    mulligan_stub = StubMulligan(
        mulligan
        or MulliganResult("mulligan", MulliganStatus.COMPLETED, "completed")
    )
    turn_stub = StubTurn(
        turn
        or PlayTurnResult(
            "turn",
            PlayTurnStatus.ENDED,
            actions=1,
            ended_turn=True,
            reason_code="end_turn",
        )
    )
    finish_stub = StubFinish(
        finish
        or FinishBattleResult(
            "finish",
            FinishBattleStatus.COMPLETED,
            LobbyPhase.RESULT,
            "completed",
        )
    )
    use_case = OneGameUseCase(
        enter_stub,
        mulligan_stub,
        turn_stub,
        finish_stub,
        lobby or StaticLobby(LobbyPhase.IN_GAME),
        state or StaticState(),
        token,
        clock=clock or (lambda: 0.0),
        sleep=sleep or (lambda _seconds: None),
    )
    return use_case, enter_stub, mulligan_stub, turn_stub, finish_stub


class OneGameContractTests(unittest.TestCase):
    def test_command_defaults_and_exact_numeric_types(self) -> None:
        command = OneGameCommand("command-1")
        self.assertEqual(0, command.deck_index)
        self.assertEqual(20, command.max_turns)
        self.assertEqual(900.0, command.timeout_seconds)
        self.assertEqual(10, command.max_actions_per_turn)

        for value in (True, 1.5, "1"):
            with self.subTest(deck_index=value):
                with self.assertRaises(TypeError):
                    OneGameCommand("command", deck_index=value)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            OneGameCommand("command", deck_index=-1)
        for value in (True, 1.5):
            with self.subTest(max_turns=value):
                with self.assertRaises(TypeError):
                    OneGameCommand("command", max_turns=value)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            OneGameCommand("command", max_turns=0)
        with self.assertRaises(ValueError):
            OneGameCommand("command", timeout_seconds=float("nan"))

    def test_result_deduplicates_evidence_and_keeps_child_results(self) -> None:
        enter = EnterBattleResult(
            "enter",
            EnterBattleStatus.COMPLETED,
            "completed",
            LobbyPhase.IN_GAME,
            evidence_ids=("a", "b"),
        )
        turn = PlayTurnResult(
            "turn",
            PlayTurnStatus.ENDED,
            actions=1,
            reason_code="end_turn",
            evidence_ids=("b", "c"),
        )
        result = OneGameResult(
            "command",
            "command",
            OneGameStatus.FAILED,
            "failure",
            turns_played=1,
            actions_played=1,
            last_lobby_phase=LobbyPhase.IN_GAME,
            evidence_ids=("a", "b", "b", "c", ""),
            enter_battle=enter,
            turn_results=(turn,),
        )

        self.assertEqual(("a", "b", "c"), result.evidence_ids)
        self.assertIs(enter, result.enter_battle)
        self.assertEqual((turn,), result.turn_results)


class OneGameUseCaseTests(unittest.TestCase):
    def test_cancelled_enter_battle_stops_before_all_later_children(self) -> None:
        use_case, enter, mulligan, turn, finish = make_use_case(
            enter=EnterBattleResult(
                "enter",
                EnterBattleStatus.CANCELLED,
                "operator_stop",
                LobbyPhase.MAIN_MENU,
            )
        )

        result = use_case.execute(OneGameCommand("cancelled-enter"))

        self.assertIs(OneGameStatus.CANCELLED, result.status)
        self.assertEqual("operator_stop", result.reason_code)
        self.assertEqual(1, enter.calls)
        self.assertEqual(0, mulligan.calls)
        self.assertEqual(0, turn.calls)
        self.assertEqual(0, finish.calls)

    def test_early_enter_battle_timeout_is_typed_failure(self) -> None:
        use_case, _, mulligan, turn, finish = make_use_case(
            enter=EnterBattleResult(
                "enter",
                EnterBattleStatus.TIMED_OUT,
                "timeout",
                LobbyPhase.UNKNOWN,
            )
        )

        result = use_case.execute(OneGameCommand("timed-enter"))

        self.assertIs(OneGameStatus.FAILED, result.status)
        self.assertEqual("enter_battle_timeout", result.reason_code)
        self.assertEqual(0, mulligan.calls)
        self.assertEqual(0, turn.calls)
        self.assertEqual(0, finish.calls)

    def test_enter_battle_timeout_after_total_deadline_is_timed_out(self) -> None:
        clock = SequenceClock([0.0, 0.0, 900.0])
        use_case, _, mulligan, turn, finish = make_use_case(
            enter=EnterBattleResult(
                "enter",
                EnterBattleStatus.TIMED_OUT,
                "timeout",
                LobbyPhase.UNKNOWN,
            ),
            clock=clock,
        )

        result = use_case.execute(
            OneGameCommand("deadline-expired", timeout_seconds=900.0)
        )

        self.assertIs(OneGameStatus.TIMED_OUT, result.status)
        self.assertEqual("total_timeout", result.reason_code)
        self.assertEqual(0, mulligan.calls)
        self.assertEqual(0, turn.calls)
        self.assertEqual(0, finish.calls)

    def test_completed_turn_is_fail_closed_and_never_settles(self) -> None:
        use_case, _, mulligan, turn, finish = make_use_case(
            enter=EnterBattleResult(
                "enter",
                EnterBattleStatus.COMPLETED,
                "completed",
                LobbyPhase.IN_GAME,
            ),
            turn=PlayTurnResult(
                "turn",
                PlayTurnStatus.COMPLETED,
                actions=2,
                reason_code="max_actions_reached",
                evidence_ids=("turn-evidence",),
            ),
        )

        result = use_case.execute(OneGameCommand("budget-turn"))

        self.assertIs(OneGameStatus.FAILED, result.status)
        self.assertEqual("turn_action_budget_exhausted", result.reason_code)
        self.assertEqual(1, mulligan.calls)
        self.assertEqual(1, turn.calls)
        self.assertEqual(1, result.turns_played)
        self.assertEqual(2, result.actions_played)
        self.assertEqual(0, finish.calls)


    def test_failed_mulligan_stops_before_turn_and_settlement(self) -> None:
        use_case, _, mulligan, turn, finish = make_use_case(
            enter=EnterBattleResult(
                "enter",
                EnterBattleStatus.COMPLETED,
                "completed",
                LobbyPhase.IN_GAME,
            ),
            mulligan=MulliganResult(
                "mulligan",
                MulliganStatus.REJECTED,
                "mulligan_surface_missing",
            ),
        )

        result = use_case.execute(OneGameCommand("failed-mulligan"))

        self.assertIs(OneGameStatus.FAILED, result.status)
        self.assertEqual("mulligan_surface_missing", result.reason_code)
        self.assertEqual(1, mulligan.calls)
        self.assertEqual(0, turn.calls)
        self.assertEqual(0, finish.calls)

    def test_finish_failure_never_reports_completed(self) -> None:
        use_case, _, _, turn, finish = make_use_case(
            enter=EnterBattleResult(
                "enter",
                EnterBattleStatus.COMPLETED,
                "completed",
                LobbyPhase.IN_GAME,
            ),
            turn=PlayTurnResult(
                "turn",
                PlayTurnStatus.ENDED,
                reason_code="game_ended",
            ),
            finish=FinishBattleResult(
                "finish",
                FinishBattleStatus.FAILED,
                LobbyPhase.IN_GAME,
                "lobby_state_error",
            ),
        )

        result = use_case.execute(OneGameCommand("failed-finish"))

        self.assertIs(OneGameStatus.FAILED, result.status)
        self.assertEqual("lobby_state_error", result.reason_code)
        self.assertEqual(1, turn.calls)
        self.assertEqual(1, finish.calls)

    def test_local_turn_end_waits_for_later_revision_then_settles(self) -> None:
        state = AdvancingState()
        use_case, _, _, turn, finish = make_use_case(
            enter=EnterBattleResult(
                "enter",
                EnterBattleStatus.COMPLETED,
                "completed",
                LobbyPhase.IN_GAME,
            ),
            turn=PlayTurnResult(
                "turn",
                PlayTurnStatus.ENDED,
                actions=1,
                reason_code="local_turn_ended",
            ),
            state=state,
        )

        result = use_case.execute(OneGameCommand("local-turn-ended"))

        self.assertIs(OneGameStatus.COMPLETED, result.status)
        self.assertEqual(1, turn.calls)
        self.assertEqual(1, finish.calls)
        self.assertEqual(1, state.wait_calls)

    def test_same_turn_new_revision_is_not_delegated_twice(self) -> None:
        state = SequenceState(
            [
                GameObservation(turn_snapshot(), 1.0),
                GameObservation(
                    replace(
                        turn_snapshot(),
                        snapshot_id="turn-snapshot-lagging",
                        log_revision=3,
                    ),
                    1.0,
                ),
                GameObservation(
                    replace(
                        turn_snapshot(),
                        snapshot_id="turn-snapshot-result",
                        log_revision=4,
                        is_my_turn=False,
                        is_end=True,
                    ),
                    1.0,
                ),
            ]
        )
        use_case, _, _, turn, finish = make_use_case(
            enter=EnterBattleResult(
                "enter",
                EnterBattleStatus.COMPLETED,
                "completed",
                LobbyPhase.IN_GAME,
            ),
            turn=PlayTurnResult(
                "turn",
                PlayTurnStatus.ENDED,
                actions=1,
                reason_code="end_turn",
            ),
            state=state,
        )

        result = use_case.execute(OneGameCommand("same-turn-revision"))

        self.assertIs(OneGameStatus.COMPLETED, result.status)
        self.assertEqual(1, turn.calls)
        self.assertEqual(1, finish.calls)
        self.assertEqual(2, state.wait_calls)

    def test_new_game_generation_fails_before_another_turn_call(self) -> None:
        state = SequenceState(
            [
                GameObservation(turn_snapshot(), 1.0),
                GameObservation(
                    replace(
                        turn_snapshot(),
                        snapshot_id="second-game-snapshot",
                        log_revision=99,
                        game_generation=8,
                    ),
                    1.0,
                ),
            ]
        )
        use_case, _, _, turn, finish = make_use_case(
            enter=EnterBattleResult(
                "enter",
                EnterBattleStatus.COMPLETED,
                "completed",
                LobbyPhase.IN_GAME,
            ),
            turn=PlayTurnResult(
                "turn",
                PlayTurnStatus.ENDED,
                actions=1,
                reason_code="end_turn",
            ),
            state=state,
        )

        result = use_case.execute(OneGameCommand("second-generation"))

        self.assertIs(OneGameStatus.FAILED, result.status)
        self.assertEqual("game_generation_changed", result.reason_code)
        self.assertEqual(1, turn.calls)
        self.assertEqual(0, finish.calls)
        self.assertEqual(1, len(result.turn_results))

    def test_cancellation_wins_when_lobby_failure_races(self) -> None:
        token = StopToken()
        use_case, _, mulligan, turn, finish = make_use_case(
            enter=EnterBattleResult(
                "enter",
                EnterBattleStatus.COMPLETED,
                "completed",
                LobbyPhase.IN_GAME,
            ),
            lobby=CancellingLobby(token),
            token=token,
        )

        result = use_case.execute(OneGameCommand("lobby-cancel"))

        self.assertIs(OneGameStatus.CANCELLED, result.status)
        self.assertEqual("lobby_cancelled", result.reason_code)
        self.assertEqual(0, mulligan.calls)
        self.assertEqual(0, turn.calls)
        self.assertEqual(0, finish.calls)

    def test_cancellation_wins_when_state_wait_returns_none(self) -> None:
        token = StopToken()
        state = CancelOnWaitState(token)
        use_case, _, _, turn, finish = make_use_case(
            enter=EnterBattleResult(
                "enter",
                EnterBattleStatus.COMPLETED,
                "completed",
                LobbyPhase.IN_GAME,
            ),
            turn=PlayTurnResult(
                "turn",
                PlayTurnStatus.ENDED,
                actions=1,
                reason_code="end_turn",
            ),
            state=state,
            token=token,
        )

        result = use_case.execute(OneGameCommand("wait-cancel"))

        self.assertIs(OneGameStatus.CANCELLED, result.status)
        self.assertEqual("wait_cancelled", result.reason_code)
        self.assertEqual(1, turn.calls)
        self.assertEqual(0, finish.calls)
        self.assertEqual(1, state.wait_calls)

    def test_cancellation_wins_when_sleeper_failure_races(self) -> None:
        token = StopToken()

        def sleep_then_fail(_seconds: float) -> None:
            token.cancel("sleep_cancelled")
            raise RuntimeError("sleeper failed with cancellation racing")

        use_case, _, mulligan, turn, finish = make_use_case(
            enter=EnterBattleResult(
                "enter",
                EnterBattleStatus.COMPLETED,
                "completed",
                LobbyPhase.IN_GAME,
            ),
            lobby=StaticLobby(LobbyPhase.DECK_SELECT),
            token=token,
            sleep=sleep_then_fail,
        )

        result = use_case.execute(OneGameCommand("sleep-cancel"))

        self.assertIs(OneGameStatus.CANCELLED, result.status)
        self.assertEqual("sleep_cancelled", result.reason_code)
        self.assertEqual(0, mulligan.calls)
        self.assertEqual(0, turn.calls)
        self.assertEqual(0, finish.calls)

    def test_malformed_clock_after_start_is_typed_clock_error(self) -> None:
        use_case, _, _, turn, finish = make_use_case(
            enter=EnterBattleResult(
                "enter",
                EnterBattleStatus.COMPLETED,
                "completed",
                LobbyPhase.IN_GAME,
            ),
            clock=SequenceClock([0.0, float("nan")]),
        )

        result = use_case.execute(OneGameCommand("bad-clock"))

        self.assertIs(OneGameStatus.FAILED, result.status)
        self.assertEqual("clock_error", result.reason_code)
        self.assertEqual(0, turn.calls)
        self.assertEqual(0, finish.calls)

    def test_prestart_cancellation_wins_over_clock_failure(self) -> None:
        token = StopToken()
        token.cancel("prestart_cancelled")

        def failing_clock() -> float:
            raise RuntimeError("clock unavailable")

        use_case, _, _, turn, finish = make_use_case(
            enter=EnterBattleResult(
                "enter",
                EnterBattleStatus.COMPLETED,
                "completed",
                LobbyPhase.IN_GAME,
            ),
            token=token,
            clock=failing_clock,
        )

        result = use_case.execute(OneGameCommand("prestart-cancel"))

        self.assertIs(OneGameStatus.CANCELLED, result.status)
        self.assertEqual("prestart_cancelled", result.reason_code)
        self.assertEqual(0, turn.calls)
        self.assertEqual(0, finish.calls)

    def test_early_lobby_timeout_is_typed_failure(self) -> None:
        use_case, _, _, turn, finish = make_use_case(
            enter=EnterBattleResult(
                "enter",
                EnterBattleStatus.COMPLETED,
                "completed",
                LobbyPhase.IN_GAME,
            ),
            lobby=TimeoutLobby(),
        )

        result = use_case.execute(OneGameCommand("lobby-timeout"))

        self.assertIs(OneGameStatus.FAILED, result.status)
        self.assertEqual("lobby_state_timeout", result.reason_code)
        self.assertEqual(0, turn.calls)
        self.assertEqual(0, finish.calls)

    def test_early_state_wait_none_is_typed_failure(self) -> None:
        use_case, _, _, turn, finish = make_use_case(
            enter=EnterBattleResult(
                "enter",
                EnterBattleStatus.COMPLETED,
                "completed",
                LobbyPhase.IN_GAME,
            ),
            turn=PlayTurnResult(
                "turn",
                PlayTurnStatus.ENDED,
                actions=1,
                reason_code="end_turn",
            ),
            state=StaticState(),
        )

        result = use_case.execute(OneGameCommand("state-wait-none"))

        self.assertIs(OneGameStatus.FAILED, result.status)
        self.assertEqual("game_state_timeout", result.reason_code)
        self.assertEqual(1, turn.calls)
        self.assertEqual(0, finish.calls)


if __name__ == "__main__":
    unittest.main()
