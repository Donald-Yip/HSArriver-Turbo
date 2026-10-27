from __future__ import annotations

import threading
import unittest
from collections.abc import Callable
from dataclasses import replace

from hslegend.application import (
    DispatchResult,
    ExecutionStatus,
    GameObservation,
)
from hslegend.application.planning import (
    ArchivedActionError,
    AttemptLimitExceeded,
    ExecutionLedger,
    ImmediateReplayBlocked,
    InFlightAttemptError,
    PlanBuilder,
    PlanExecutor,
    PlanValidator,
    action_key_for_plan,
)
from hslegend.application.runtime import ActionGate, ActionLeaseHeld, StopToken
from hslegend.domain import ActionIntent, Condition, GameSnapshot, PlanStep
from hslegend.plugins import build_default_registry
from tests.application.fakes import CardCatalogFake, RunJournalFake
from tests.domain.support import snapshot


class ScriptedGameState:
    def __init__(
        self,
        observations: list[GameObservation],
        *,
        on_wait: Callable[[], None] | None = None,
    ) -> None:
        self._observations = observations
        self._index = 0
        self._on_wait = on_wait
        self.wait_calls: list[tuple[int, float]] = []

    def current(self) -> GameObservation:
        return self._observations[self._index]

    def wait_for_revision_change(
        self, after_revision: int, timeout_seconds: float
    ) -> GameObservation | None:
        self.wait_calls.append((after_revision, timeout_seconds))
        if self._on_wait is not None:
            self._on_wait()
        if self._index + 1 >= len(self._observations):
            return None
        candidate = self._observations[self._index + 1]
        if candidate.snapshot.log_revision <= after_revision:
            return candidate
        self._index += 1
        return candidate


class ScriptedDispatch:
    def __init__(self, results: list[DispatchResult | Exception]) -> None:
        self._results = list(results)
        self.calls: list[tuple[PlanStep, GameSnapshot]] = []

    def dispatch(self, step: PlanStep, snapshot: GameSnapshot) -> DispatchResult:
        self.calls.append((step, snapshot))
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class SequenceClock:
    def __init__(self, values: list[float] | None = None) -> None:
        self._values = list(values or ())
        self._value = 10.0

    def __call__(self) -> float:
        if self._values:
            return self._values.pop(0)
        value = self._value
        self._value += 0.25
        return value


class RaisingJournal:
    def __init__(self, message: str = "journal unavailable") -> None:
        self._message = message
        self.calls = 0

    def append(self, record: object) -> object:
        self.calls += 1
        raise RuntimeError(self._message)


class CountingLedger(ExecutionLedger):
    def __init__(self) -> None:
        super().__init__()
        self.complete_calls = 0

    def complete(self, attempt, status):
        self.complete_calls += 1
        return super().complete(attempt, status)


class BlockingLedger(ExecutionLedger):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.complete_calls = 0

    def complete(self, attempt, status):
        self.complete_calls += 1
        self.entered.set()
        if not self.release.wait(2.0):
            raise AssertionError("blocking ledger was not released")
        return super().complete(attempt, status)


class BlockingJournal:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def append(self, record: object) -> object:
        self.calls += 1
        self.entered.set()
        if not self.release.wait(2.0):
            raise AssertionError("blocking journal was not released")
        return object()


class CancelDuringStepValidationValidator(PlanValidator):
    def __init__(self, catalog, callback) -> None:
        super().__init__(catalog)
        self._callback = callback

    def validate_step(self, plan, step, snapshot):
        result = super().validate_step(plan, step, snapshot)
        self._callback()
        return result


class PlanningExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.before = snapshot()
        self.after = snapshot(
            snapshot_id="snap-2",
            revision=8,
            turn=6,
            is_my_turn=False,
        )
        self.registry = build_default_registry()
        self.catalog = CardCatalogFake()
        self.builder = PlanBuilder(self.registry, self.catalog)
        self.validator = PlanValidator(self.catalog)

    def end_turn_plan(self, game: GameSnapshot | None = None):
        game = game or self.before
        return self.builder.build(
            game,
            ActionIntent("end_turn", intent_id="intent-end-turn"),
        )

    def multi_step_plan(self, second_conditions: tuple[Condition, ...]):
        plan = self.end_turn_plan()
        first = PlanStep(
            "multi-step-1",
            plan.intent,
            preconditions=(Condition("is_my_turn", expected=True),),
        )
        second = PlanStep(
            "multi-step-2",
            plan.intent,
            preconditions=second_conditions,
        )
        return replace(
            plan,
            plan_id="multi-plan",
            steps=(first, second),
            postconditions=(),
        )

    def executor(
        self,
        dispatch: ScriptedDispatch,
        state: ScriptedGameState,
        journal: RunJournalFake,
        ledger: ExecutionLedger,
        *,
        clock: SequenceClock | None = None,
    ) -> PlanExecutor:
        return PlanExecutor(
            dispatch,
            state,
            journal,
            ledger,
            self.validator,
            clock=clock or SequenceClock(),
        )

    def observation(self, value: GameSnapshot) -> GameObservation:
        return GameObservation(value, 1.0)

    def test_successful_execution_dispatches_and_confirms(self) -> None:
        plan = self.end_turn_plan()
        dispatch = ScriptedDispatch([DispatchResult.sent("dispatch-1", "evidence-1")])
        state = ScriptedGameState(
            [self.observation(self.before), self.observation(self.after)]
        )
        journal = RunJournalFake()
        ledger = ExecutionLedger()

        record = self.executor(dispatch, state, journal, ledger).execute(
            plan,
            stop_token=StopToken(),
            action_gate=ActionGate(),
            session_id="session-1",
            correlation_id="correlation-1",
            game_id="game-1",
        )

        self.assertEqual(ExecutionStatus.EXECUTED_CONFIRMED, record.status)
        self.assertEqual("postcondition_confirmed", record.error_code)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual((record,), journal.records)
        self.assertEqual(ExecutionStatus.EXECUTED_CONFIRMED, ledger.last_attempt(action_key_for_plan(plan)).status)
        self.assertEqual("snap-2", record.after_snapshot_id)
        self.assertIn("evidence-1", record.evidence_ids)

    def test_invalid_stale_plan_is_rejected_without_dispatch(self) -> None:
        plan = self.end_turn_plan(self.before)
        newer = snapshot(snapshot_id="snap-2", revision=8)
        dispatch = ScriptedDispatch([])
        state = ScriptedGameState([self.observation(newer)])
        journal = RunJournalFake()

        record = self.executor(
            dispatch, state, journal, ExecutionLedger()
        ).execute(plan, stop_token=StopToken(), action_gate=ActionGate())

        self.assertEqual(ExecutionStatus.REJECTED, record.status)
        self.assertEqual("stale_snapshot", record.error_code)
        self.assertEqual([], dispatch.calls)
        self.assertEqual(1, len(journal.records))

    def test_dispatch_exception_is_unconfirmed_and_journaled(self) -> None:
        plan = self.end_turn_plan()
        dispatch = ScriptedDispatch([RuntimeError("input backend unavailable")])
        state = ScriptedGameState([self.observation(self.before)])
        journal = RunJournalFake()

        record = self.executor(
            dispatch, state, journal, ExecutionLedger()
        ).execute(plan, stop_token=StopToken(), action_gate=ActionGate())

        self.assertEqual(ExecutionStatus.EXECUTED_UNCONFIRMED, record.status)
        self.assertEqual("input backend unavailable", record.error_code)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual(1, len(journal.records))

    def test_unchanged_revision_is_unconfirmed_and_blocks_immediate_replay(self) -> None:
        plan = self.end_turn_plan()
        dispatch = ScriptedDispatch([DispatchResult.sent("dispatch-1")])
        state = ScriptedGameState(
            [self.observation(self.before), self.observation(self.before)]
        )
        journal = RunJournalFake()
        ledger = ExecutionLedger()
        executor = self.executor(dispatch, state, journal, ledger)
        args = {"stop_token": StopToken(), "action_gate": ActionGate()}

        record = executor.execute(plan, **args)
        self.assertEqual(ExecutionStatus.EXECUTED_UNCONFIRMED, record.status)
        self.assertEqual("snapshot_unchanged", record.error_code)

        with self.assertRaises(ImmediateReplayBlocked):
            executor.execute(plan, **args)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual(1, len(journal.records))

    def test_regressed_revision_fails_closed(self) -> None:
        plan = self.end_turn_plan()
        regressed = snapshot(snapshot_id="snap-regressed", revision=6)
        dispatch = ScriptedDispatch([DispatchResult.sent("dispatch-1")])
        state = ScriptedGameState(
            [self.observation(self.before), self.observation(regressed)]
        )
        journal = RunJournalFake()

        record = self.executor(
            dispatch, state, journal, ExecutionLedger()
        ).execute(plan, stop_token=StopToken(), action_gate=ActionGate())

        self.assertEqual(ExecutionStatus.EXECUTED_UNCONFIRMED, record.status)
        self.assertEqual("snapshot_regressed", record.error_code)

    def test_confirmed_plan_never_replays(self) -> None:
        plan = self.end_turn_plan()
        dispatch = ScriptedDispatch([DispatchResult.sent("dispatch-1")])
        state = ScriptedGameState(
            [self.observation(self.before), self.observation(self.after)]
        )
        journal = RunJournalFake()
        ledger = ExecutionLedger()
        executor = self.executor(dispatch, state, journal, ledger)
        args = {"stop_token": StopToken(), "action_gate": ActionGate()}

        executor.execute(plan, **args)
        with self.assertRaises(ImmediateReplayBlocked):
            executor.execute(plan, **args)

        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual(1, len(journal.records))

    def test_unconfirmed_replay_requires_authorization(self) -> None:
        plan = self.end_turn_plan()
        unchanged_state = ScriptedGameState(
            [self.observation(self.before), self.observation(self.before)]
        )
        dispatch = ScriptedDispatch(
            [DispatchResult.sent("dispatch-1"), DispatchResult.sent("dispatch-2")]
        )
        journal = RunJournalFake()
        ledger = ExecutionLedger()
        executor = self.executor(dispatch, unchanged_state, journal, ledger)
        args = {"stop_token": StopToken(), "action_gate": ActionGate()}

        first = executor.execute(plan, **args)
        self.assertEqual(ExecutionStatus.EXECUTED_UNCONFIRMED, first.status)
        key = action_key_for_plan(plan)
        ledger.authorize_retry(key, evidence_id="user-confirmed-retry")

        retry_current = snapshot(
            snapshot_id="snap-retry", revision=8, turn=6, is_my_turn=True
        )
        after_second = snapshot(
            snapshot_id="snap-3", revision=9, turn=7, is_my_turn=False
        )
        second_state = ScriptedGameState(
            [self.observation(retry_current), self.observation(after_second)]
        )
        second_plan = self.end_turn_plan(retry_current)
        second_executor = self.executor(dispatch, second_state, journal, ledger)
        second = second_executor.execute(second_plan, **args)

        self.assertEqual(ExecutionStatus.EXECUTED_CONFIRMED, second.status)
        self.assertEqual(2, len(dispatch.calls))
        self.assertEqual(2, len(ledger.attempts(key)))

    def test_cancelled_token_records_cancelled_without_dispatch(self) -> None:
        plan = self.end_turn_plan()
        dispatch = ScriptedDispatch([])
        state = ScriptedGameState([self.observation(self.before)])
        journal = RunJournalFake()
        ledger = ExecutionLedger()
        token = StopToken()
        token.cancel("user stopped")

        record = self.executor(dispatch, state, journal, ledger).execute(
            plan,
            stop_token=token,
            action_gate=ActionGate(),
        )

        self.assertEqual(ExecutionStatus.CANCELLED, record.status)
        self.assertEqual("user stopped", record.error_code)
        self.assertEqual([], dispatch.calls)
        self.assertEqual(ExecutionStatus.CANCELLED, ledger.last_attempt(action_key_for_plan(plan)).status)

    def test_false_first_step_precondition_fails_without_dispatch(self) -> None:
        plan = self.end_turn_plan()
        step = replace(
            plan.steps[0],
            preconditions=(Condition("is_my_turn", expected=False),),
        )
        plan = replace(plan, plan_id="false-first", steps=(step,), preconditions=())
        dispatch = ScriptedDispatch([])
        state = ScriptedGameState([self.observation(self.before)])
        journal = RunJournalFake()
        ledger = ExecutionLedger()

        record = self.executor(dispatch, state, journal, ledger).execute(
            plan,
            stop_token=StopToken(),
            action_gate=ActionGate(),
        )

        self.assertEqual(ExecutionStatus.FAILED, record.status)
        self.assertEqual("precondition_failed", record.error_code)
        self.assertEqual([], dispatch.calls)
        self.assertEqual(1, len(journal.records))
        self.assertEqual(ExecutionStatus.FAILED, ledger.last_attempt(action_key_for_plan(plan)).status)

    def test_false_precondition_after_prior_step_is_unconfirmed(self) -> None:
        after_mid = snapshot(
            snapshot_id="snap-mid",
            revision=8,
            turn=6,
            is_my_turn=True,
        )
        plan = self.multi_step_plan(
            (Condition("turn_number", expected=999),)
        )
        dispatch = ScriptedDispatch([DispatchResult.sent("dispatch-1")])
        state = ScriptedGameState(
            [self.observation(self.before), self.observation(after_mid)]
        )
        journal = RunJournalFake()
        ledger = ExecutionLedger()

        record = self.executor(dispatch, state, journal, ledger).execute(
            plan,
            stop_token=StopToken(),
            action_gate=ActionGate(),
        )

        self.assertEqual(ExecutionStatus.EXECUTED_UNCONFIRMED, record.status)
        self.assertEqual("precondition_failed", record.error_code)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual(1, len(journal.records))
        self.assertEqual(ExecutionStatus.EXECUTED_UNCONFIRMED, ledger.last_attempt(action_key_for_plan(plan)).status)

    def test_exception_after_prior_step_dispatch_is_unconfirmed(self) -> None:
        after_mid = snapshot(
            snapshot_id="snap-mid",
            revision=8,
            turn=6,
            is_my_turn=True,
        )
        plan = self.multi_step_plan(
            (Condition("is_my_turn", expected=True),)
        )
        dispatch = ScriptedDispatch(
            [
                DispatchResult.sent("dispatch-1"),
                RuntimeError("second dispatch exploded"),
            ]
        )
        state = ScriptedGameState(
            [self.observation(self.before), self.observation(after_mid)]
        )
        journal = RunJournalFake()
        ledger = ExecutionLedger()
        executor = self.executor(dispatch, state, journal, ledger)
        args = {"stop_token": StopToken(), "action_gate": ActionGate()}

        record = executor.execute(plan, **args)

        self.assertEqual(ExecutionStatus.EXECUTED_UNCONFIRMED, record.status)
        self.assertEqual("second dispatch exploded", record.error_code)
        self.assertEqual(2, len(dispatch.calls))
        self.assertEqual(1, len(journal.records))
        with self.assertRaises(ImmediateReplayBlocked):
            executor.execute(plan, **args)

    def test_ledger_rejects_second_begin_while_attempt_in_flight(self) -> None:
        plan = self.end_turn_plan()
        ledger = ExecutionLedger()
        key = action_key_for_plan(plan)

        ledger.begin(key)
        with self.assertRaises(InFlightAttemptError):
            ledger.begin(key)

    def test_second_execute_cannot_dispatch_while_first_completion_blocked(self) -> None:
        plan = self.end_turn_plan()
        dispatch = ScriptedDispatch([DispatchResult.sent("dispatch-1")])
        state = ScriptedGameState(
            [self.observation(self.before), self.observation(self.after)]
        )
        ledger = BlockingLedger()
        journal = RunJournalFake()
        gate = ActionGate()
        token = StopToken()
        executor = self.executor(dispatch, state, journal, ledger)
        errors: list[BaseException] = []

        def run_first() -> None:
            try:
                executor.execute(plan, stop_token=token, action_gate=gate)
            except BaseException as exc:
                errors.append(exc)

        first = threading.Thread(target=run_first, name="r05-first-execute")
        first.start()
        self.assertTrue(ledger.entered.wait(2.0), "first completion should block")
        try:
            key = action_key_for_plan(plan)
            self.assertEqual(
                ExecutionStatus.NOT_EXECUTED,
                ledger.last_attempt(key).status,
            )
            with self.assertRaises(ActionLeaseHeld):
                executor.execute(plan, stop_token=token, action_gate=gate)
            self.assertEqual(1, len(dispatch.calls))
            self.assertEqual(1, len(ledger.attempts(key)))
        finally:
            ledger.release.set()
            first.join(timeout=2.0)

        self.assertFalse(first.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(1, len(dispatch.calls))
        self.assertIsNone(gate.current_lease())

    def test_second_execute_cannot_dispatch_while_first_journal_blocked(self) -> None:
        plan = self.end_turn_plan()
        dispatch = ScriptedDispatch([DispatchResult.sent("dispatch-1")])
        state = ScriptedGameState(
            [self.observation(self.before), self.observation(self.after)]
        )
        journal = BlockingJournal()
        ledger = ExecutionLedger()
        gate = ActionGate()
        token = StopToken()
        executor = self.executor(dispatch, state, journal, ledger)
        errors: list[BaseException] = []

        def run_first() -> None:
            try:
                executor.execute(plan, stop_token=token, action_gate=gate)
            except BaseException as exc:
                errors.append(exc)

        first = threading.Thread(target=run_first, name="r05-journal-execute")
        first.start()
        self.assertTrue(journal.entered.wait(2.0), "first journal write should block")
        try:
            key = action_key_for_plan(plan)
            self.assertEqual(
                ExecutionStatus.EXECUTED_CONFIRMED,
                ledger.last_attempt(key).status,
            )
            with self.assertRaises(ActionLeaseHeld):
                executor.execute(plan, stop_token=token, action_gate=gate)
            self.assertEqual(1, len(dispatch.calls))
            self.assertEqual(1, len(ledger.attempts(key)))
        finally:
            journal.release.set()
            first.join(timeout=2.0)

        self.assertFalse(first.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual(1, journal.calls)
        self.assertIsNone(gate.current_lease())

    def test_cancellation_during_step_validation_stops_before_dispatch(self) -> None:
        plan = self.end_turn_plan()
        dispatch = ScriptedDispatch([])
        state = ScriptedGameState([self.observation(self.before)])
        token = StopToken()
        validator = CancelDuringStepValidationValidator(
            self.catalog,
            lambda: token.cancel("cancel while validating"),
        )
        journal = RunJournalFake()
        ledger = ExecutionLedger()
        executor = PlanExecutor(
            dispatch,
            state,
            journal,
            ledger,
            validator,
            clock=SequenceClock(),
        )

        record = executor.execute(
            plan,
            stop_token=token,
            action_gate=ActionGate(),
        )

        self.assertEqual(ExecutionStatus.CANCELLED, record.status)
        self.assertEqual("cancel while validating", record.error_code)
        self.assertEqual([], dispatch.calls)
        self.assertEqual(ExecutionStatus.CANCELLED, ledger.last_attempt(action_key_for_plan(plan)).status)

    def test_budget_expiry_during_step_validation_stops_before_dispatch(self) -> None:
        base_plan = self.end_turn_plan()
        plan = replace(
            base_plan,
            plan_id="validation-timeout-plan",
            timeout_seconds=1.0,
            steps=(replace(base_plan.steps[0], timeout_seconds=10.0),),
        )
        dispatch = ScriptedDispatch([])
        state = ScriptedGameState([self.observation(self.before)])
        journal = RunJournalFake()
        ledger = ExecutionLedger()
        clock = SequenceClock([0.0, 0.0, 0.0, 1.25, 1.25])

        record = self.executor(
            dispatch,
            state,
            journal,
            ledger,
            clock=clock,
        ).execute(
            plan,
            stop_token=StopToken(),
            action_gate=ActionGate(),
        )

        self.assertEqual(ExecutionStatus.FAILED, record.status)
        self.assertEqual("plan_timeout", record.error_code)
        self.assertEqual([], dispatch.calls)
        self.assertEqual(ExecutionStatus.FAILED, ledger.last_attempt(action_key_for_plan(plan)).status)

    def test_journal_failure_propagates_after_one_ledger_completion(self) -> None:
        plan = self.end_turn_plan()
        dispatch = ScriptedDispatch([DispatchResult.sent("dispatch-1")])
        state = ScriptedGameState(
            [self.observation(self.before), self.observation(self.after)]
        )
        journal = RaisingJournal("journal unavailable")
        ledger = CountingLedger()
        gate = ActionGate()

        with self.assertRaisesRegex(RuntimeError, "journal unavailable"):
            self.executor(dispatch, state, journal, ledger).execute(
                plan,
                stop_token=StopToken(),
                action_gate=gate,
            )

        self.assertEqual(1, journal.calls)
        self.assertEqual(1, ledger.complete_calls)
        self.assertIsNone(gate.current_lease())
        self.assertEqual(ExecutionStatus.EXECUTED_CONFIRMED, ledger.last_attempt(action_key_for_plan(plan)).status)

    def test_cancellation_after_wait_is_unconfirmed_and_preserves_reason(self) -> None:
        plan = self.end_turn_plan()
        token = StopToken()
        dispatch = ScriptedDispatch([DispatchResult.sent("dispatch-1")])
        state = ScriptedGameState(
            [self.observation(self.before), self.observation(self.after)],
            on_wait=lambda: token.cancel("stop after revision"),
        )
        journal = RunJournalFake()
        ledger = ExecutionLedger()

        record = self.executor(dispatch, state, journal, ledger).execute(
            plan,
            stop_token=token,
            action_gate=ActionGate(),
        )

        self.assertEqual(ExecutionStatus.EXECUTED_UNCONFIRMED, record.status)
        self.assertEqual("stop after revision", record.error_code)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual(ExecutionStatus.EXECUTED_UNCONFIRMED, ledger.last_attempt(action_key_for_plan(plan)).status)

    def test_plan_level_timeout_is_enforced_after_dispatch(self) -> None:
        base_plan = self.end_turn_plan()
        plan = replace(
            base_plan,
            plan_id="timeout-plan",
            timeout_seconds=1.0,
            steps=(replace(base_plan.steps[0], timeout_seconds=10.0),),
        )
        dispatch = ScriptedDispatch([DispatchResult.sent("dispatch-1")])
        state = ScriptedGameState([self.observation(self.before)])
        journal = RunJournalFake()
        ledger = ExecutionLedger()
        clock = SequenceClock([0.0, 0.0, 0.0, 0.0, 0.0, 1.25, 1.25])

        record = self.executor(
            dispatch,
            state,
            journal,
            ledger,
            clock=clock,
        ).execute(
            plan,
            stop_token=StopToken(),
            action_gate=ActionGate(),
        )

        self.assertEqual(ExecutionStatus.EXECUTED_UNCONFIRMED, record.status)
        self.assertEqual("plan_timeout", record.error_code)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual([], state.wait_calls)
        self.assertEqual(ExecutionStatus.EXECUTED_UNCONFIRMED, ledger.last_attempt(action_key_for_plan(plan)).status)

    def test_wait_timeout_is_clamped_to_remaining_plan_budget(self) -> None:
        base_plan = self.end_turn_plan()
        plan = replace(
            base_plan,
            plan_id="clamped-timeout-plan",
            timeout_seconds=5.0,
            steps=(replace(base_plan.steps[0], timeout_seconds=10.0),),
        )
        dispatch = ScriptedDispatch([DispatchResult.sent("dispatch-1")])
        state = ScriptedGameState(
            [self.observation(self.before), self.observation(self.after)]
        )
        clock = SequenceClock([0.0, 0.0, 0.0, 0.0, 0.0, 3.0, 3.25, 3.5, 3.75])

        record = self.executor(
            dispatch,
            state,
            RunJournalFake(),
            ExecutionLedger(),
            clock=clock,
        ).execute(
            plan,
            stop_token=StopToken(),
            action_gate=ActionGate(),
        )

        self.assertEqual(ExecutionStatus.EXECUTED_CONFIRMED, record.status)
        self.assertEqual([(7, 2.0)], state.wait_calls)

    def test_archived_action_is_not_executed(self) -> None:
        plan = self.end_turn_plan()
        ledger = ExecutionLedger()
        key = action_key_for_plan(plan)
        ledger.archive(key)

        with self.assertRaises(ArchivedActionError):
            self.executor(
                ScriptedDispatch([]),
                ScriptedGameState([self.observation(self.before)]),
                RunJournalFake(),
                ledger,
            ).execute(plan, stop_token=StopToken(), action_gate=ActionGate())

    def test_attempt_limit_is_enforced(self) -> None:
        plan = self.end_turn_plan()
        failed = DispatchResult.failed("backend_failed")
        dispatch = ScriptedDispatch([failed, failed])
        state = ScriptedGameState([self.observation(self.before)])
        journal = RunJournalFake()
        ledger = ExecutionLedger(max_attempts=2)
        executor = self.executor(dispatch, state, journal, ledger)
        args = {"stop_token": StopToken(), "action_gate": ActionGate()}

        self.assertEqual(ExecutionStatus.FAILED, executor.execute(plan, **args).status)
        self.assertEqual(ExecutionStatus.FAILED, executor.execute(plan, **args).status)
        with self.assertRaises(AttemptLimitExceeded):
            executor.execute(plan, **args)
        self.assertEqual(2, len(journal.records))


if __name__ == "__main__":
    unittest.main()
