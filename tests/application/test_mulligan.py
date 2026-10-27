from __future__ import annotations

import unittest
from dataclasses import replace

from hslegend.application import (
    DispatchResult,
    ExecutionStatus,
    GameObservation,
    RecommendationMode,
    RecommendationObservation,
)
from hslegend.application.planning import ExecutionLedger, ImmediateReplayBlocked, PlanExecutor, PlanValidator
from hslegend.application.runtime import ActionGate, StopToken
from hslegend.bootstrap import build_mulligan_use_case
from hslegend.application.use_cases.mulligan import (
    MulliganCommand,
    MulliganInstructionError,
    MulliganResult,
    MulliganStatus,
    MulliganUseCase,
    parse_mulligan_instruction,
    validate_mulligan_selection,
)
from hslegend.domain import ChoiceView, EntityRef, GameSnapshot, PlayerView
from tests.application.fakes import (
    ActionDispatchFake,
    CardCatalogFake,
    RecommendationSourceFake,
    RunJournalFake,
)


def mulligan_snapshot(
    *,
    hand_size: int = 3,
    snapshot_id: str = "snap-mulligan",
    revision: int = 7,
    with_surface: bool = True,
) -> GameSnapshot:
    hand = tuple(
        EntityRef(100 + index, f"hand-card-{index}", "friendly", "hand", index + 1)
        for index in range(hand_size)
    )
    hero = EntityRef(1, "hero-friendly", "friendly", "hero", 0)
    opponent_hero = EntityRef(2, "hero-opponent", "opponent", "hero", 0)
    choices = (
        ChoiceView(
            choice_id="mulligan-choice",
            options=(),
            choice_type="mulligan",
            min_choices=0,
            max_choices=hand_size,
        ),
    ) if with_surface else ()
    return GameSnapshot(
        schema_version=1,
        snapshot_id=snapshot_id,
        log_revision=revision,
        game_generation=11,
        observed_at=100.0 + revision,
        turn_number=0,
        is_my_turn=False,
        is_end=False,
        my_player=PlayerView(
            player_id=10,
            side="friendly",
            hero=hero,
            hand=hand,
        ),
        opponent=PlayerView(
            player_id=20,
            side="opponent",
            hero=opponent_hero,
        ),
        choices=choices,
    )


def recommendation(
    snapshot: GameSnapshot,
    instruction: str,
    *,
    mode: RecommendationMode = RecommendationMode.MULLIGAN,
    evidence_id: str = "rec-evidence",
    snapshot_id: str | None = None,
) -> RecommendationObservation:
    return RecommendationObservation(
        mode=mode,
        instruction=instruction,
        confidence=1.0,
        evidence_id=evidence_id,
        snapshot_id=snapshot.snapshot_id if snapshot_id is None else snapshot_id,
    )


class AdvancingState:
    def __init__(self, current: GameSnapshot, after: GameSnapshot | None) -> None:
        self._current = current
        self._after = after
        self.wait_calls: list[tuple[int, float]] = []

    def current(self) -> GameObservation:
        return GameObservation(self._current, 1.0)

    def wait_for_revision_change(
        self,
        after_revision: int,
        timeout_seconds: float,
    ) -> GameObservation | None:
        self.wait_calls.append((after_revision, timeout_seconds))
        if self._after is None:
            return None
        if self._after.log_revision <= after_revision:
            return GameObservation(self._current, 1.0)
        self._current = self._after
        return GameObservation(self._after, 1.0)


class RaisingState(AdvancingState):
    def current(self) -> GameObservation:
        raise RuntimeError("state unavailable")


class RaisingRecommendation:
    def recommend(self, snapshot, mode):
        raise RuntimeError("recommendation unavailable")


class RaisingJournal:
    def append(self, record):
        raise RuntimeError("journal unavailable")


class ChangingCurrentState(AdvancingState):
    def __init__(self, first: GameSnapshot, second: GameSnapshot) -> None:
        super().__init__(first, None)
        self._second = second
        self.current_calls = 0

    def current(self) -> GameObservation:
        self.current_calls += 1
        if self.current_calls == 1:
            return GameObservation(self._current, 1.0)
        return GameObservation(self._second, 1.0)


class MulliganInstructionTests(unittest.TestCase):
    def test_keep_all_preserves_every_card(self) -> None:
        self.assertEqual((), parse_mulligan_instruction("保留全部卡牌"))
        self.assertEqual((), validate_mulligan_selection((), 3))
        self.assertEqual((), validate_mulligan_selection((), 5))

    def test_replacements_are_unique_and_sorted(self) -> None:
        instruction = "替换3号位卡牌\n替换1号位卡牌\n替换3号位卡牌"
        self.assertEqual((1, 3), parse_mulligan_instruction(instruction))
        self.assertEqual((0, 2), validate_mulligan_selection((3, 1, 3), 3))

    def test_legacy_slot_bounds_match_three_and_five_card_hands(self) -> None:
        self.assertEqual((2,), validate_mulligan_selection((3,), 3))
        self.assertEqual((3,), validate_mulligan_selection((4,), 5))
        with self.assertRaises(MulliganInstructionError) as three_card:
            validate_mulligan_selection((4,), 3)
        self.assertEqual("mulligan_slot_invalid", three_card.exception.reason_code)
        with self.assertRaises(MulliganInstructionError) as five_card:
            validate_mulligan_selection((5,), 5)
        self.assertEqual("mulligan_slot_invalid", five_card.exception.reason_code)
        with self.assertRaises(MulliganInstructionError) as hand_size:
            validate_mulligan_selection((1,), 4)
        self.assertEqual("unsupported_hand_size", hand_size.exception.reason_code)


class MulliganUseCaseTests(unittest.TestCase):
    def make_use_case(
        self,
        *,
        snapshot: GameSnapshot | None = None,
        instruction: str = "保留全部卡牌",
        results: list[DispatchResult] | None = None,
        after: GameSnapshot | None = None,
        mode: RecommendationMode = RecommendationMode.MULLIGAN,
        rec_snapshot_id: str | None = None,
        state=None,
        gate: ActionGate | None = None,
        token: StopToken | None = None,
        ledger: ExecutionLedger | None = None,
        journal: RunJournalFake | None = None,
    ) -> tuple[MulliganUseCase, ActionDispatchFake, RunJournalFake, ExecutionLedger, AdvancingState]:
        snapshot = snapshot or mulligan_snapshot()
        after = after if after is not None else mulligan_snapshot(snapshot_id="snap-after", revision=8)
        state = state or AdvancingState(snapshot, after)
        journal = journal or RunJournalFake()
        ledger = ledger or ExecutionLedger()
        dispatch = ActionDispatchFake(results or [DispatchResult.sent("dispatch", "dispatch-evidence")])
        recommendation_source = RecommendationSourceFake(
            {
                RecommendationMode.MULLIGAN: recommendation(
                    snapshot,
                    instruction,
                    mode=mode,
                    snapshot_id=rec_snapshot_id,
                )
            }
        )
        validator = PlanValidator(CardCatalogFake())
        executor = PlanExecutor(dispatch, state, journal, ledger, validator, clock=lambda: 10.0)
        use_case = MulliganUseCase(
            recommendation_source,
            state,
            gate or ActionGate(),
            token or StopToken(),
            executor,
        )
        return use_case, dispatch, journal, ledger, state

    def assert_rejected_without_dispatch(
        self,
        result: MulliganResult,
        reason_code: str,
        dispatch: ActionDispatchFake,
    ) -> None:
        self.assertIs(MulliganStatus.REJECTED, result.status)
        self.assertEqual(reason_code, result.reason_code)
        self.assertEqual([], dispatch.calls)

    def test_keep_all_dispatches_only_confirm(self) -> None:
        use_case, dispatch, journal, _, _ = self.make_use_case()

        result = use_case.execute(MulliganCommand("mulligan-1", 5.0))

        self.assertIs(MulliganStatus.COMPLETED, result.status)
        self.assertEqual("postcondition_confirmed", result.reason_code)
        self.assertEqual((), result.selected_indexes)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual("mulligan_confirm", dispatch.calls[0][0].intent.action_type)
        self.assertTrue(dispatch.calls[0][0].wait_for_change)
        self.assertEqual(1, len(journal.records))

    def test_replacements_are_sorted_and_journal_evidence_is_complete(self) -> None:
        snapshot = mulligan_snapshot(hand_size=5)
        instruction = "替换4号位卡牌\n替换1号位卡牌\n替换4号位卡牌"
        use_case, dispatch, journal, _, _ = self.make_use_case(
            snapshot=snapshot,
            instruction=instruction,
            results=[
                DispatchResult.sent("replace-1", "dispatch-replace-1"),
                DispatchResult.sent("replace-4", "dispatch-replace-4"),
                DispatchResult.sent("confirm", "dispatch-confirm"),
            ],
        )

        result = use_case.execute(MulliganCommand("mulligan-2", 5.0))

        self.assertIs(MulliganStatus.COMPLETED, result.status)
        self.assertEqual((0, 3), result.selected_indexes)
        self.assertEqual(
            ["mulligan_replace", "mulligan_replace", "mulligan_confirm"],
            [step.intent.action_type for step, _ in dispatch.calls],
        )
        self.assertEqual(
            [snapshot.my_player.hand[0], snapshot.my_player.hand[3]],
            [step.intent.source for step, _ in dispatch.calls[:2]],
        )
        self.assertFalse(dispatch.calls[0][0].wait_for_change)
        self.assertFalse(dispatch.calls[1][0].wait_for_change)
        self.assertTrue(dispatch.calls[2][0].wait_for_change)
        self.assertEqual("journal-1", result.journal_record_id)
        self.assertTrue(result.journal_durable)
        self.assertFalse(result.journal_duplicate)
        record = journal.records[0]
        self.assertIn("rec-evidence", record.evidence_ids)
        self.assertIn("dispatch-replace-1", record.evidence_ids)
        self.assertIn("dispatch-replace-4", record.evidence_ids)
        self.assertIn("dispatch-confirm", record.evidence_ids)
        self.assertIn("rec-evidence", result.evidence_ids)
        self.assertIsNotNone(result.execution_record)


    def test_source_failures_are_typed_and_dispatch_nothing(self) -> None:
        snapshot = mulligan_snapshot()
        unchanged = AdvancingState(snapshot, None)
        journal = RunJournalFake()
        dispatch = ActionDispatchFake([DispatchResult.sent("unused")])
        ledger = ExecutionLedger()
        validator = PlanValidator(CardCatalogFake())
        executor = PlanExecutor(
            dispatch,
            unchanged,
            journal,
            ledger,
            validator,
            clock=lambda: 10.0,
        )
        use_case = MulliganUseCase(
            RaisingRecommendation(),
            unchanged,
            ActionGate(),
            StopToken(),
            executor,
        )

        result = use_case.execute(MulliganCommand("recommendation-failure", 5.0))

        self.assertIs(MulliganStatus.FAILED, result.status)
        self.assertEqual("recommendation_error", result.reason_code)
        self.assertEqual([], dispatch.calls)
        self.assertEqual((), journal.records)

        state_failure_journal = RunJournalFake()
        state_failure = RaisingState(snapshot, None)
        state_failure_executor = PlanExecutor(
            dispatch,
            state_failure,
            state_failure_journal,
            ledger,
            validator,
            clock=lambda: 10.0,
        )
        recommendation_source = RecommendationSourceFake(
            {
                RecommendationMode.MULLIGAN: recommendation(
                    snapshot,
                    "保留全部卡牌",
                )
            }
        )
        state_use_case = MulliganUseCase(
            recommendation_source,
            state_failure,
            ActionGate(),
            StopToken(),
            state_failure_executor,
        )

        state_result = state_use_case.execute(MulliganCommand("state-failure", 5.0))

        self.assertIs(MulliganStatus.FAILED, state_result.status)
        self.assertEqual("game_state_error", state_result.reason_code)
        self.assertEqual([], dispatch.calls)
        self.assertEqual((), state_failure_journal.records)

    def test_bootstrap_builds_mulligan_without_legacy_imports(self) -> None:
        snapshot = mulligan_snapshot()
        state = AdvancingState(snapshot, mulligan_snapshot(snapshot_id="snap-after", revision=8))
        dispatch = ActionDispatchFake(
            [DispatchResult.sent("bootstrap-confirm", "bootstrap-evidence")]
        )
        journal = RunJournalFake()
        recommendation_source = RecommendationSourceFake(
            {
                RecommendationMode.MULLIGAN: recommendation(
                    snapshot,
                    "保留全部卡牌",
                )
            }
        )

        use_case = build_mulligan_use_case(
            recommendation_source,
            state,
            dispatch,
            journal,
            card_catalog=CardCatalogFake(),
            clock=lambda: 10.0,
        )
        result = use_case.execute(MulliganCommand("bootstrap-mulligan", 5.0))

        self.assertIsInstance(use_case, MulliganUseCase)
        self.assertIs(MulliganStatus.COMPLETED, result.status)
        self.assertEqual(1, len(dispatch.calls))

    def test_validation_failures_dispatch_nothing(self) -> None:
        cases = (
            ("empty instruction", mulligan_snapshot(), "", RecommendationMode.MULLIGAN, None, "empty_instruction"),
            ("ambiguous keep", mulligan_snapshot(), "保留全部卡牌\n替换1号位卡牌", RecommendationMode.MULLIGAN, None, "ambiguous_instruction"),
            ("bad instruction", mulligan_snapshot(), "替换0号位卡牌", RecommendationMode.MULLIGAN, None, "unsupported_instruction"),
            ("wrong mode", mulligan_snapshot(), "保留全部卡牌", RecommendationMode.TURN, None, "wrong_mode"),
            ("missing surface", mulligan_snapshot(with_surface=False), "保留全部卡牌", RecommendationMode.MULLIGAN, None, "mulligan_surface_missing"),
            ("unsupported hand", mulligan_snapshot(hand_size=4), "保留全部卡牌", RecommendationMode.MULLIGAN, None, "unsupported_hand_size"),
            ("out of range", mulligan_snapshot(hand_size=5), "替换5号位卡牌", RecommendationMode.MULLIGAN, None, "mulligan_slot_invalid"),
            ("stale recommendation", mulligan_snapshot(), "保留全部卡牌", RecommendationMode.MULLIGAN, "snap-old", "stale_recommendation"),
        )
        for name, snapshot, instruction, mode, rec_snapshot_id, reason_code in cases:
            with self.subTest(name=name):
                use_case, dispatch, journal, _, _ = self.make_use_case(
                    snapshot=snapshot,
                    instruction=instruction,
                    mode=mode,
                    rec_snapshot_id=rec_snapshot_id,
                )
                result = use_case.execute(MulliganCommand(f"mulligan-{name}", 5.0))
                self.assert_rejected_without_dispatch(result, reason_code, dispatch)
                self.assertEqual((), journal.records)

    def test_changed_current_snapshot_is_stale_and_dispatches_nothing(self) -> None:
        first = mulligan_snapshot()
        second = mulligan_snapshot(snapshot_id="snap-new", revision=8)
        state = ChangingCurrentState(first, second)
        use_case, dispatch, _, _, _ = self.make_use_case(snapshot=first, state=state)

        result = use_case.execute(MulliganCommand("mulligan-stale", 5.0))

        self.assert_rejected_without_dispatch(result, "stale_snapshot", dispatch)

    def test_cancellation_before_dispatch_is_typed_and_dispatches_nothing(self) -> None:
        token = StopToken()
        token.cancel("user stopped")
        use_case, dispatch, journal, _, _ = self.make_use_case(token=token)

        result = use_case.execute(MulliganCommand("mulligan-cancel", 5.0))

        self.assertIs(MulliganStatus.CANCELLED, result.status)
        self.assertEqual("user stopped", result.reason_code)
        self.assertEqual([], dispatch.calls)
        self.assertEqual((), journal.records)

    def test_gate_busy_dispatches_nothing(self) -> None:
        gate = ActionGate()
        lease = gate.acquire("other", "other-command")
        try:
            use_case, dispatch, journal, _, _ = self.make_use_case(gate=gate)
            result = use_case.execute(MulliganCommand("mulligan-gate", 5.0))
        finally:
            gate.release(lease)

        self.assertIs(MulliganStatus.GATE_BUSY, result.status)
        self.assertEqual("action_gate_busy", result.reason_code)
        self.assertEqual([], dispatch.calls)
        self.assertEqual((), journal.records)

    def test_release_failure_preserves_primary_status(self) -> None:
        class FailingReleaseGate(ActionGate):
            def release(self, lease) -> None:
                raise RuntimeError("gate release failed")

        use_case, dispatch, journal, _, _ = self.make_use_case(
            gate=FailingReleaseGate()
        )

        result = use_case.execute(MulliganCommand("mulligan-release", 5.0))

        self.assertIs(MulliganStatus.COMPLETED, result.status)
        self.assertEqual("RuntimeError: gate release failed", result.release_error)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual(1, len(journal.records))

    def test_ledger_error_surfaces_release_failure(self) -> None:
        class FailingReleaseGate(ActionGate):
            def release(self, lease) -> None:
                raise RuntimeError("release failed")

        snapshot = mulligan_snapshot()
        state = AdvancingState(
            snapshot,
            mulligan_snapshot(snapshot_id="snap-after", revision=8),
        )
        dispatch = ActionDispatchFake([DispatchResult.sent("must-not-run")])
        recommendation_source = RecommendationSourceFake(
            {
                RecommendationMode.MULLIGAN: recommendation(
                    snapshot,
                    "保留全部卡牌",
                )
            }
        )
        ledger = ExecutionLedger()
        ledger.archive(f"{snapshot.game_generation}:mulligan-ledger")
        executor = PlanExecutor(
            dispatch,
            state,
            RunJournalFake(),
            ledger,
            PlanValidator(CardCatalogFake()),
            clock=lambda: 10.0,
        )
        use_case = MulliganUseCase(
            recommendation_source,
            state,
            FailingReleaseGate(),
            StopToken(),
            executor,
        )

        result = use_case.execute(MulliganCommand("mulligan-ledger", 5.0))

        self.assertIs(MulliganStatus.REJECTED, result.status)
        self.assertEqual("archived_action", result.reason_code)
        self.assertEqual("RuntimeError: release failed", result.release_error)
        self.assertEqual([], dispatch.calls)

    def test_executor_exception_surfaces_release_failure(self) -> None:
        class FailingReleaseGate(ActionGate):
            def release(self, lease) -> None:
                raise RuntimeError("release failed too")

        snapshot = mulligan_snapshot()
        state = AdvancingState(
            snapshot,
            mulligan_snapshot(snapshot_id="snap-after", revision=8),
        )
        dispatch = ActionDispatchFake([DispatchResult.sent("confirm")])
        recommendation_source = RecommendationSourceFake(
            {
                RecommendationMode.MULLIGAN: recommendation(
                    snapshot,
                    "保留全部卡牌",
                )
            }
        )
        executor = PlanExecutor(
            dispatch,
            state,
            RaisingJournal(),
            ExecutionLedger(),
            PlanValidator(CardCatalogFake()),
            clock=lambda: 10.0,
        )
        use_case = MulliganUseCase(
            recommendation_source,
            state,
            FailingReleaseGate(),
            StopToken(),
            executor,
        )

        result = use_case.execute(MulliganCommand("mulligan-journal", 5.0))

        self.assertIs(MulliganStatus.EXECUTED_UNCONFIRMED, result.status)
        self.assertEqual("execution_error", result.reason_code)
        self.assertEqual("RuntimeError: release failed too", result.release_error)
        self.assertEqual(1, len(dispatch.calls))

    def test_confirmation_timeout_is_unconfirmed_and_never_replays(self) -> None:
        snapshot = mulligan_snapshot()
        state = AdvancingState(snapshot, None)
        use_case, dispatch, journal, _, _ = self.make_use_case(snapshot=snapshot, state=state)

        first = use_case.execute(MulliganCommand("mulligan-timeout", 5.0))
        second = use_case.execute(MulliganCommand("mulligan-timeout", 5.0))

        self.assertIs(MulliganStatus.EXECUTED_UNCONFIRMED, first.status)
        self.assertEqual("revision_timeout", first.reason_code)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual(1, len(journal.records))
        self.assertIs(MulliganStatus.REJECTED, second.status)
        self.assertEqual("immediate_replay_blocked", second.reason_code)
        self.assertEqual(1, len(dispatch.calls))


if __name__ == "__main__":
    unittest.main()
