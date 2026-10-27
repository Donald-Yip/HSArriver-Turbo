from __future__ import annotations

import unittest
from collections.abc import Callable

from hslegend.application.dto import (
    DispatchResult,
    LobbyPhase,
    LobbyState,
)
from hslegend.application.runtime import ActionGate, StopToken
from hslegend.application.use_cases.enter_battle import (
    EnterBattleCommand,
    EnterBattleStatus,
    EnterBattleUseCase,
    LobbyAction,
    build_lobby_step,
)
from hslegend.bootstrap import Container, build_container
from hslegend.domain import GameSnapshot, PlanStep


def lobby_state(
    phase: LobbyPhase,
    *,
    observed_at: float = 0.0,
    evidence_id: str = "",
) -> LobbyState:
    return LobbyState(
        phase=phase,
        observed_at=observed_at,
        confidence=1.0 if phase is not LobbyPhase.UNKNOWN else 0.0,
        evidence_id=evidence_id,
    )


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class QueuedLobby:
    def __init__(
        self,
        states: list[LobbyState],
        *,
        on_get: Callable[[int, LobbyState], None] | None = None,
        timeout_on_call: int | None = None,
    ) -> None:
        if not states:
            raise ValueError("at least one state is required")
        self._states = list(states)
        self._on_get = on_get
        self._timeout_on_call = timeout_on_call
        self.calls: list[LobbyState] = []
        self.timeout_calls: list[float] = []

    def get_state(self, *, timeout_seconds: float) -> LobbyState:
        self.timeout_calls.append(timeout_seconds)
        if self._timeout_on_call == len(self.calls) + 1:
            raise TimeoutError("lobby read blocked")
        index = min(len(self.calls), len(self._states) - 1)
        state = self._states[index]
        self.calls.append(state)
        if self._on_get is not None:
            self._on_get(len(self.calls), state)
        return state


class ScriptedDispatch:
    def __init__(
        self,
        results: list[DispatchResult | Exception],
        *,
        on_dispatch: Callable[[int, PlanStep, GameSnapshot], None] | None = None,
        timeout_on_call: int | None = None,
    ) -> None:
        self._results = list(results)
        self._on_dispatch = on_dispatch
        self._timeout_on_call = timeout_on_call
        self.calls: list[tuple[PlanStep, GameSnapshot]] = []
        self.timeout_calls: list[float] = []

    def dispatch(
        self,
        step: PlanStep,
        snapshot: GameSnapshot,
        *,
        timeout_seconds: float,
    ) -> DispatchResult:
        self.calls.append((step, snapshot))
        self.timeout_calls.append(timeout_seconds)
        if self._on_dispatch is not None:
            self._on_dispatch(len(self.calls), step, snapshot)
        if self._timeout_on_call == len(self.calls):
            raise TimeoutError("dispatch blocked")
        if not self._results:
            raise AssertionError("unexpected dispatch")
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class EnterBattleUseCaseTests(unittest.TestCase):
    def make_use_case(
        self,
        *,
        states: list[LobbyState],
        results: list[DispatchResult | Exception],
        token: StopToken | None = None,
        gate: ActionGate | None = None,
        clock: FakeClock | None = None,
        on_get: Callable[[int, LobbyState], None] | None = None,
        on_dispatch: Callable[[int, PlanStep, GameSnapshot], None] | None = None,
        lobby_timeout_on_call: int | None = None,
        dispatch_timeout_on_call: int | None = None,
    ) -> tuple[EnterBattleUseCase, QueuedLobby, ScriptedDispatch, StopToken, ActionGate, FakeClock]:
        clock = clock or FakeClock()
        token = token or StopToken()
        gate = gate or ActionGate()
        lobby = QueuedLobby(
            states,
            on_get=on_get,
            timeout_on_call=lobby_timeout_on_call,
        )
        dispatch = ScriptedDispatch(
            results,
            on_dispatch=on_dispatch,
            timeout_on_call=dispatch_timeout_on_call,
        )
        use_case = EnterBattleUseCase(
            lobby=lobby,
            dispatch=dispatch,
            action_gate=gate,
            stop_token=token,
            clock=clock,
        )
        return use_case, lobby, dispatch, token, gate, clock

    def test_happy_path_dispatches_semantic_actions_in_order(self) -> None:
        use_case, lobby, dispatch, _, gate, clock = self.make_use_case(
            states=[
                lobby_state(LobbyPhase.MAIN_MENU, evidence_id="menu-frame"),
                lobby_state(LobbyPhase.DECK_SELECT, evidence_id="deck-frame"),
            ],
            results=[
                DispatchResult.sent("dispatch-enter", "enter-evidence"),
                DispatchResult.sent("dispatch-deck", "deck-evidence"),
                DispatchResult.sent("dispatch-start", "start-evidence"),
            ],
        )

        result = use_case.execute(
            EnterBattleCommand(
                deck_index=3,
                start_match=True,
                timeout_seconds=5.0,
                poll_interval_seconds=0.25,
            )
        )

        self.assertTrue(result.ok)
        self.assertIs(EnterBattleStatus.COMPLETED, result.status)
        self.assertEqual("completed", result.reason_code)
        self.assertEqual(LobbyPhase.DECK_SELECT, result.phase)
        self.assertEqual(2, len(lobby.calls))
        self.assertEqual(
            [
                LobbyAction.ENTER_TRADITIONAL,
                LobbyAction.SELECT_DECK,
                LobbyAction.START_MATCH,
            ],
            [record.action for record in result.dispatches],
        )
        self.assertEqual(
            [None, 3, None],
            [step.intent.choice_index for step, _ in dispatch.calls],
        )
        self.assertEqual(
            ("enter-evidence", "deck-evidence", "start-evidence"),
            result.evidence_ids,
        )
        self.assertIsInstance(dispatch.calls[0][1], GameSnapshot)
        self.assertEqual([5.0, 5.0], lobby.timeout_calls)
        self.assertEqual([5.0, 5.0, 5.0], dispatch.timeout_calls)
        self.assertIsNone(gate.current_lease())
        self.assertEqual([], clock.sleeps)

    def test_select_deck_without_start_match_stops_after_selection(self) -> None:
        use_case, _, dispatch, _, _, _ = self.make_use_case(
            states=[lobby_state(LobbyPhase.MAIN_MENU), lobby_state(LobbyPhase.DECK_SELECT)],
            results=[
                DispatchResult.sent("dispatch-enter"),
                DispatchResult.sent("dispatch-deck"),
            ],
        )

        result = use_case.execute(EnterBattleCommand(deck_index=0))

        self.assertTrue(result.ok)
        self.assertEqual(
            [LobbyAction.ENTER_TRADITIONAL, LobbyAction.SELECT_DECK],
            [record.action for record in result.dispatches],
        )
        self.assertEqual(2, len(dispatch.calls))

    def test_unknown_lobby_state_fails_closed_without_dispatch(self) -> None:
        use_case, lobby, dispatch, _, gate, _ = self.make_use_case(
            states=[lobby_state(LobbyPhase.UNKNOWN), lobby_state(LobbyPhase.MAIN_MENU)],
            results=[DispatchResult.sent("unused")],
        )

        result = use_case.execute(EnterBattleCommand(deck_index=1))

        self.assertIs(EnterBattleStatus.FAILED, result.status)
        self.assertEqual("lobby_unknown", result.reason_code)
        self.assertEqual(1, len(lobby.calls))
        self.assertEqual([], dispatch.calls)
        self.assertIsNone(gate.current_lease())

    def test_dispatched_rejection_stops_sequence(self) -> None:
        use_case, _, dispatch, _, _, _ = self.make_use_case(
            states=[lobby_state(LobbyPhase.MAIN_MENU), lobby_state(LobbyPhase.DECK_SELECT)],
            results=[DispatchResult.rejected("mode_unavailable", "evidence-1")],
        )

        result = use_case.execute(EnterBattleCommand(deck_index=0))

        self.assertIs(EnterBattleStatus.FAILED, result.status)
        self.assertEqual("dispatch_rejected", result.reason_code)
        self.assertEqual("mode_unavailable", result.detail_code)
        self.assertIs(LobbyAction.ENTER_TRADITIONAL, result.failed_action)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual(("evidence-1",), result.evidence_ids)

    def test_dispatch_exception_is_a_typed_failure(self) -> None:
        use_case, _, dispatch, _, _, _ = self.make_use_case(
            states=[lobby_state(LobbyPhase.MAIN_MENU), lobby_state(LobbyPhase.DECK_SELECT)],
            results=[RuntimeError("input unavailable")],
        )

        result = use_case.execute(EnterBattleCommand(deck_index=0))

        self.assertIs(EnterBattleStatus.FAILED, result.status)
        self.assertEqual("dispatch_exception", result.reason_code)
        self.assertEqual("dispatch_raised", result.detail_code)
        self.assertEqual(1, len(dispatch.calls))

    def test_timeout_is_bounded_by_total_deadline_and_fake_clock(self) -> None:
        clock = FakeClock(now=100.0)
        use_case, lobby, dispatch, _, gate, _ = self.make_use_case(
            states=[lobby_state(LobbyPhase.MATCHING)],
            results=[DispatchResult.sent("unused")],
            clock=clock,
        )

        result = use_case.execute(
            EnterBattleCommand(
                deck_index=0,
                timeout_seconds=1.0,
                poll_interval_seconds=0.25,
            )
        )

        self.assertIs(EnterBattleStatus.TIMED_OUT, result.status)
        self.assertEqual("timeout", result.reason_code)
        self.assertEqual("timeout_waiting_main_menu", result.detail_code)
        self.assertAlmostEqual(1.0, result.elapsed_seconds)
        self.assertEqual([0.25, 0.25, 0.25, 0.25], clock.sleeps)
        self.assertGreaterEqual(len(lobby.calls), 4)
        self.assertEqual([], dispatch.calls)
        self.assertIsNone(gate.current_lease())

    def test_blocking_lobby_port_timeout_is_terminal_and_uses_remaining_budget(self) -> None:
        use_case, lobby, dispatch, _, gate, _ = self.make_use_case(
            states=[lobby_state(LobbyPhase.MAIN_MENU)],
            results=[DispatchResult.sent("unused")],
            lobby_timeout_on_call=1,
        )

        result = use_case.execute(
            EnterBattleCommand(
                deck_index=0,
                timeout_seconds=2.0,
                poll_interval_seconds=0.25,
            )
        )

        self.assertIs(EnterBattleStatus.TIMED_OUT, result.status)
        self.assertEqual("timeout", result.reason_code)
        self.assertEqual("timeout_waiting_main_menu", result.detail_code)
        self.assertEqual([], dispatch.calls)
        self.assertEqual([2.0], lobby.timeout_calls)
        self.assertIsNone(gate.current_lease())

    def test_blocking_dispatch_port_timeout_stops_before_retry_or_next_step(self) -> None:
        use_case, lobby, dispatch, _, gate, _ = self.make_use_case(
            states=[lobby_state(LobbyPhase.MAIN_MENU), lobby_state(LobbyPhase.DECK_SELECT)],
            results=[DispatchResult.sent("unused")],
            dispatch_timeout_on_call=1,
        )

        result = use_case.execute(
            EnterBattleCommand(
                deck_index=0,
                start_match=True,
                timeout_seconds=2.0,
                poll_interval_seconds=0.25,
            )
        )

        self.assertIs(EnterBattleStatus.TIMED_OUT, result.status)
        self.assertEqual("timeout", result.reason_code)
        self.assertEqual("timeout_in_enter_traditional", result.detail_code)
        self.assertIs(LobbyAction.ENTER_TRADITIONAL, result.failed_action)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual([2.0], dispatch.timeout_calls)
        self.assertEqual(1, len(lobby.calls))
        self.assertIsNone(gate.current_lease())

    def test_slow_lobby_read_cannot_complete_after_deadline(self) -> None:
        clock = FakeClock()

        def slow_read(_index: int, _state: LobbyState) -> None:
            clock.now = 2.0

        use_case, _, dispatch, _, gate, _ = self.make_use_case(
            states=[lobby_state(LobbyPhase.MAIN_MENU)],
            results=[DispatchResult.sent("unused")],
            clock=clock,
            on_get=slow_read,
        )

        result = use_case.execute(
            EnterBattleCommand(
                deck_index=0,
                timeout_seconds=1.0,
                poll_interval_seconds=0.25,
            )
        )

        self.assertIs(EnterBattleStatus.TIMED_OUT, result.status)
        self.assertEqual("timeout_waiting_main_menu", result.detail_code)
        self.assertEqual([], dispatch.calls)
        self.assertIsNone(gate.current_lease())

    def test_slow_dispatch_cannot_trigger_next_action_after_deadline(self) -> None:
        clock = FakeClock()

        def slow_dispatch(index: int, _step: PlanStep, _snapshot: GameSnapshot) -> None:
            if index == 1:
                clock.now = 2.0

        use_case, _, dispatch, _, gate, _ = self.make_use_case(
            states=[lobby_state(LobbyPhase.MAIN_MENU), lobby_state(LobbyPhase.DECK_SELECT)],
            results=[
                DispatchResult.sent("dispatch-enter"),
                DispatchResult.sent("unused"),
            ],
            clock=clock,
            on_dispatch=slow_dispatch,
        )

        result = use_case.execute(
            EnterBattleCommand(
                deck_index=0,
                start_match=True,
                timeout_seconds=1.0,
                poll_interval_seconds=0.25,
            )
        )

        self.assertIs(EnterBattleStatus.TIMED_OUT, result.status)
        self.assertEqual("timeout_after_enter_traditional", result.detail_code)
        self.assertIs(LobbyAction.ENTER_TRADITIONAL, result.failed_action)
        self.assertEqual(1, len(dispatch.calls))
        self.assertIsNone(gate.current_lease())

    def test_cancellation_between_dispatch_and_wait_prevents_later_dispatch(self) -> None:
        token = StopToken()

        def cancel_after_enter(index: int, _step: PlanStep, _snapshot: GameSnapshot) -> None:
            if index == 1:
                token.cancel("operator-stop")

        use_case, lobby, dispatch, _, gate, _ = self.make_use_case(
            states=[lobby_state(LobbyPhase.MAIN_MENU), lobby_state(LobbyPhase.DECK_SELECT)],
            results=[
                DispatchResult.sent("dispatch-enter", "enter-sent"),
                DispatchResult.sent("unused"),
            ],
            token=token,
            on_dispatch=cancel_after_enter,
        )

        result = use_case.execute(EnterBattleCommand(deck_index=0))

        self.assertIs(EnterBattleStatus.CANCELLED, result.status)
        self.assertEqual("cancelled", result.reason_code)
        self.assertEqual("cancelled_after_dispatch", result.detail_code)
        self.assertIs(LobbyAction.ENTER_TRADITIONAL, result.failed_action)
        self.assertEqual(1, len(result.dispatches))
        self.assertEqual(("enter-sent",), result.evidence_ids)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual(1, len(lobby.calls))
        self.assertIsNone(gate.current_lease())

    def test_cancellation_during_lobby_read_prevents_first_dispatch(self) -> None:
        token = StopToken()

        def cancel_on_menu(index: int, state: LobbyState) -> None:
            if index == 1 and state.phase is LobbyPhase.MAIN_MENU:
                token.cancel("stop-before-click")

        use_case, _, dispatch, _, _, _ = self.make_use_case(
            states=[lobby_state(LobbyPhase.MAIN_MENU)],
            results=[DispatchResult.sent("unused")],
            token=token,
            on_get=cancel_on_menu,
        )

        result = use_case.execute(EnterBattleCommand(deck_index=0))

        self.assertIs(EnterBattleStatus.CANCELLED, result.status)
        self.assertEqual([], dispatch.calls)

    def test_gate_contention_returns_typed_failure_without_lobby_reads(self) -> None:
        gate = ActionGate()
        held = gate.acquire("other-operation", "other-command")
        use_case, lobby, dispatch, _, _, _ = self.make_use_case(
            states=[lobby_state(LobbyPhase.MAIN_MENU)],
            results=[DispatchResult.sent("unused")],
            gate=gate,
        )

        result = use_case.execute(EnterBattleCommand(deck_index=0))

        self.assertIs(EnterBattleStatus.GATE_BUSY, result.status)
        self.assertEqual("action_gate_busy", result.reason_code)
        self.assertEqual([], lobby.calls)
        self.assertEqual([], dispatch.calls)
        self.assertEqual(held, gate.current_lease())
        gate.release(held)

    def test_release_failure_is_reported_without_masking_primary_status(self) -> None:
        class FailingReleaseGate(ActionGate):
            def release(self, lease) -> None:
                raise RuntimeError("gate release failed")

        gate = FailingReleaseGate()
        use_case, _, dispatch, _, _, _ = self.make_use_case(
            states=[lobby_state(LobbyPhase.MAIN_MENU), lobby_state(LobbyPhase.DECK_SELECT)],
            results=[
                DispatchResult.sent("dispatch-enter"),
                DispatchResult.sent("dispatch-deck"),
            ],
            gate=gate,
        )

        result = use_case.execute(EnterBattleCommand(deck_index=0))

        self.assertIs(EnterBattleStatus.COMPLETED, result.status)
        self.assertEqual("completed", result.reason_code)
        self.assertEqual("RuntimeError: gate release failed", result.release_error)
        self.assertEqual(2, len(dispatch.calls))

    def test_bootstrap_wires_injected_ports_without_legacy_factories(self) -> None:
        clock = FakeClock()
        lobby = QueuedLobby([lobby_state(LobbyPhase.MAIN_MENU), lobby_state(LobbyPhase.DECK_SELECT)])
        dispatch = ScriptedDispatch(
            [
                DispatchResult.sent("dispatch-enter"),
                DispatchResult.sent("dispatch-deck"),
            ]
        )
        gate = ActionGate()
        token = StopToken()

        container = build_container(
            lobby=lobby,
            dispatch=dispatch,
            action_gate=gate,
            stop_token=token,
            clock=clock,
        )

        self.assertIsInstance(container, Container)
        self.assertIsInstance(container.enter_battle, EnterBattleUseCase)
        result = container.enter_battle.execute(EnterBattleCommand(deck_index=2))
        self.assertTrue(result.ok)
        self.assertIsNone(gate.current_lease())

    def test_lobby_step_is_declarative_and_validated(self) -> None:
        step = build_lobby_step(LobbyAction.SELECT_DECK, choice_index=4, timeout_seconds=2.0)

        self.assertEqual("select_deck", step.intent.action_type)
        self.assertEqual(4, step.intent.choice_index)
        with self.assertRaises(ValueError):
            build_lobby_step(LobbyAction.SELECT_DECK)
        with self.assertRaises(ValueError):
            build_lobby_step(LobbyAction.ENTER_TRADITIONAL, choice_index=1)


if __name__ == "__main__":
    unittest.main()
