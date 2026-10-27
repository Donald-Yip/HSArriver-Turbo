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
from hslegend.application.planning import ExecutionLedger, PlanBuilder, PlanExecutor, PlanValidator
from hslegend.application.runtime import ActionGate, StopToken
from hslegend.application.use_cases.turn import (
    PlayTurnCommand,
    PlayTurnStatus,
    PlayTurnUseCase,
)
from hslegend.bootstrap import build_turn_use_case
from hslegend.domain import CardCapabilitySet, CardDefinition, EntityRef, GameSnapshot, PlayerView
from hslegend.plugins import build_default_registry
from tests.application.fakes import (
    ActionDispatchFake,
    CardCatalogFake,
    RunJournalFake,
)


def game_snapshot(
    *,
    snapshot_id: str = "turn-snap-1",
    revision: int = 7,
    is_my_turn: bool = True,
    hand_card: str = "card-plain",
    board_card: str = "board-card",
) -> GameSnapshot:
    friendly_hero = EntityRef(1, "hero-friendly", "friendly", "hero", 0)
    opponent_hero = EntityRef(2, "hero-opponent", "opponent", "hero", 0)
    return GameSnapshot(
        schema_version=1,
        snapshot_id=snapshot_id,
        log_revision=revision,
        game_generation=9,
        observed_at=100.0 + revision,
        turn_number=5,
        is_my_turn=is_my_turn,
        is_end=False,
        my_player=PlayerView(
            player_id=10,
            side="friendly",
            hero=friendly_hero,
            mana=5,
            hand=(EntityRef(100, hand_card, "friendly", "hand", 1),),
            board=(EntityRef(200, board_card, "friendly", "board", 1),),
        ),
        opponent=PlayerView(
            player_id=20,
            side="opponent",
            hero=opponent_hero,
            board=(EntityRef(300, "enemy-card", "opponent", "board", 1),),
        ),
    )


class ScriptedState:
    def __init__(self, snapshots: list[GameSnapshot]) -> None:
        if not snapshots:
            raise ValueError("snapshots are required")
        self._snapshots = list(snapshots)
        self._index = 0
        self.wait_calls: list[tuple[int, float]] = []

    def current(self) -> GameObservation:
        return GameObservation(self._snapshots[self._index], 1.0)

    def wait_for_revision_change(
        self, after_revision: int, timeout_seconds: float
    ) -> GameObservation | None:
        self.wait_calls.append((after_revision, timeout_seconds))
        if self._index + 1 >= len(self._snapshots):
            return None
        candidate = self._snapshots[self._index + 1]
        if candidate.log_revision <= after_revision:
            return GameObservation(self._snapshots[self._index], 1.0)
        self._index += 1
        return GameObservation(candidate, 1.0)


class StaleAfterState(ScriptedState):
    """Executor sees the confirmed revision, but current() remains stale."""

    def __init__(self, snapshots: list[GameSnapshot]) -> None:
        super().__init__(snapshots)
        self._returned_after = False

    def wait_for_revision_change(self, after_revision: int, timeout_seconds: float):
        self.wait_calls.append((after_revision, timeout_seconds))
        if self._returned_after or len(self._snapshots) < 2:
            return None
        self._returned_after = True
        return GameObservation(self._snapshots[1], 1.0)


class DynamicRecommendation:
    def __init__(
        self,
        instruction: str,
        *,
        mode: RecommendationMode = RecommendationMode.TURN,
        snapshot_id: str | None = None,
    ) -> None:
        self.instruction = instruction
        self.mode = mode
        self.snapshot_id = snapshot_id
        self.calls: list[tuple[GameSnapshot, RecommendationMode]] = []

    def recommend(self, snapshot: GameSnapshot, mode: RecommendationMode):
        self.calls.append((snapshot, mode))
        return RecommendationObservation(
            mode=self.mode,
            instruction=self.instruction,
            confidence=1.0,
            evidence_id=f"rec-{len(self.calls)}",
            snapshot_id=snapshot.snapshot_id if self.snapshot_id is None else self.snapshot_id,
        )


class RecordingBuilder(PlanBuilder):
    def __init__(self, registry, catalog) -> None:
        super().__init__(registry, catalog)
        self.calls = 0

    def build(self, snapshot, intent):
        self.calls += 1
        return super().build(snapshot, intent)


class CancelAfterExecuteExecutor(PlanExecutor):
    def __init__(self, *args, token: StopToken, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._turn_token = token

    def execute(self, *args, **kwargs):
        record = super().execute(*args, **kwargs)
        self._turn_token.cancel("cancelled_after_confirmation")
        return record


class PlayTurnUseCaseTests(unittest.TestCase):
    def make_use_case(
        self,
        *,
        state: ScriptedState,
        recommendation: DynamicRecommendation,
        dispatch: ActionDispatchFake | None = None,
        journal: RunJournalFake | None = None,
        gate: ActionGate | None = None,
        token: StopToken | None = None,
        clock=None,
        executor_factory=None,
    ):
        dispatch = dispatch or ActionDispatchFake([DispatchResult.sent("dispatch-1")])
        journal = journal or RunJournalFake()
        catalog = CardCatalogFake(
            [
                CardDefinition("card-plain", card_type="minion"),
                CardDefinition(
                    "card-target",
                    card_type="minion",
                    capabilities=CardCapabilitySet(targetable=True),
                ),
                CardDefinition(
                    "card-ambiguous",
                    card_type="location",
                    capabilities=CardCapabilitySet(targetable=True, location=True),
                ),
                CardDefinition(
                    "card-location",
                    card_type="location",
                    capabilities=CardCapabilitySet(location=True),
                ),
            ]
        )
        builder = RecordingBuilder(build_default_registry(), catalog)
        executor = PlanExecutor(
            dispatch,
            state,
            journal,
            ExecutionLedger(),
            PlanValidator(catalog),
            clock=clock,
        )
        if executor_factory is not None:
            executor = executor_factory(executor)
        use_case = PlayTurnUseCase(
            recommendation,
            state,
            builder,
            executor,
            gate or ActionGate(),
            token or StopToken(),
            clock=clock,
        )
        return use_case, builder, dispatch, journal

    def test_end_turn_uses_end_turn_plugin_and_stops(self) -> None:
        before = game_snapshot()
        after = game_snapshot(snapshot_id="turn-snap-2", revision=8, is_my_turn=False)
        dispatch = ActionDispatchFake([DispatchResult.sent("end")])
        use_case, builder, dispatch, journal = self.make_use_case(
            state=ScriptedState([before, after]),
            recommendation=DynamicRecommendation("结束回合"),
            dispatch=dispatch,
        )

        result = use_case.execute(
            PlayTurnCommand("turn-end", max_actions=5, timeout_seconds=10.0)
        )

        self.assertIs(PlayTurnStatus.ENDED, result.status)
        self.assertTrue(result.ended_turn)
        self.assertEqual(1, result.actions)
        self.assertEqual(1, builder.calls)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual("end_turn", dispatch.calls[0][0].intent.action_type)
        self.assertTrue(journal.records[0].plan_id.startswith("end-turn:"))
        self.assertEqual(1, len(result.action_records))

    def test_max_actions_bounds_multi_action_turn_and_journals_each_action(self) -> None:
        snapshots = [
            game_snapshot(snapshot_id="turn-snap-1", revision=7),
            game_snapshot(snapshot_id="turn-snap-2", revision=8),
            game_snapshot(snapshot_id="turn-snap-3", revision=9),
        ]
        dispatch = ActionDispatchFake(
            [DispatchResult.sent("play-1"), DispatchResult.sent("play-2")]
        )
        use_case, builder, dispatch, journal = self.make_use_case(
            state=ScriptedState(snapshots),
            recommendation=DynamicRecommendation("打出1号位随从"),
            dispatch=dispatch,
        )

        result = use_case.execute(
            PlayTurnCommand("turn-budget", max_actions=2, timeout_seconds=10.0)
        )

        self.assertIs(PlayTurnStatus.COMPLETED, result.status)
        self.assertEqual("max_actions_reached", result.reason_code)
        self.assertEqual(2, result.actions)
        self.assertEqual(2, builder.calls)
        self.assertEqual(2, len(dispatch.calls))
        self.assertEqual(2, len(journal.records))
        self.assertEqual(2, len(result.action_records))
        self.assertNotEqual(
            result.action_records[0].action_id,
            result.action_records[1].action_id,
        )

    def test_wrong_mode_stale_recommendation_and_missing_turn_dispatch_nothing(self) -> None:
        before = game_snapshot()
        cases = [
            (DynamicRecommendation("结束回合", mode=RecommendationMode.MULLIGAN), "wrong_mode"),
            (DynamicRecommendation("结束回合", snapshot_id="other-snapshot"), "stale_recommendation"),
        ]
        for recommendation, reason in cases:
            with self.subTest(reason=reason):
                dispatch = ActionDispatchFake([DispatchResult.sent("must-not-run")])
                use_case, _, dispatch, journal = self.make_use_case(
                    state=ScriptedState([before]),
                    recommendation=recommendation,
                    dispatch=dispatch,
                )
                result = use_case.execute(PlayTurnCommand("turn-reject", 2, 10.0))
                self.assertIs(PlayTurnStatus.REJECTED, result.status)
                self.assertEqual(reason, result.reason_code)
                self.assertEqual([], dispatch.calls)
                self.assertEqual((), journal.records)

        recommendation = DynamicRecommendation("结束回合")
        dispatch = ActionDispatchFake([DispatchResult.sent("must-not-run")])
        use_case, _, dispatch, journal = self.make_use_case(
            state=ScriptedState([game_snapshot(is_my_turn=False)]),
            recommendation=recommendation,
            dispatch=dispatch,
        )
        result = use_case.execute(PlayTurnCommand("turn-not-mine", 2, 10.0))
        self.assertIs(PlayTurnStatus.ENDED, result.status)
        self.assertEqual("local_turn_ended", result.reason_code)
        self.assertEqual([], dispatch.calls)
        self.assertEqual((), journal.records)
        self.assertEqual([], recommendation.calls)

    def test_unconfirmed_action_stops_and_identical_retry_is_blocked(self) -> None:
        before = game_snapshot()
        dispatch = ActionDispatchFake([DispatchResult.sent("play-once")])
        use_case, _, dispatch, journal = self.make_use_case(
            state=ScriptedState([before]),
            recommendation=DynamicRecommendation("打出1号位随从"),
            dispatch=dispatch,
        )
        command = PlayTurnCommand("turn-unconfirmed", 3, 10.0)

        first = use_case.execute(command)
        second = use_case.execute(command)

        self.assertIs(PlayTurnStatus.UNCONFIRMED, first.status)
        self.assertEqual("revision_timeout", first.reason_code)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual(1, len(journal.records))
        self.assertIs(PlayTurnStatus.REJECTED, second.status)
        self.assertEqual("immediate_replay_blocked", second.reason_code)
        self.assertEqual(1, len(dispatch.calls))

    def test_cancellation_before_dispatch_and_after_confirmation_are_terminal(self) -> None:
        before = game_snapshot()
        token = StopToken()
        token.cancel("stopped_before_dispatch")
        dispatch = ActionDispatchFake([DispatchResult.sent("must-not-run")])
        use_case, _, dispatch, journal = self.make_use_case(
            state=ScriptedState([before]),
            recommendation=DynamicRecommendation("打出1号位随从"),
            dispatch=dispatch,
            token=token,
        )
        result = use_case.execute(PlayTurnCommand("turn-cancel-before", 2, 10.0))
        self.assertIs(PlayTurnStatus.CANCELLED, result.status)
        self.assertEqual([], dispatch.calls)
        self.assertEqual((), journal.records)

        before = game_snapshot()
        after = game_snapshot(snapshot_id="turn-snap-2", revision=8)
        token = StopToken()
        dispatch = ActionDispatchFake([DispatchResult.sent("play")])
        use_case, _, dispatch, journal = self.make_use_case(
            state=ScriptedState([before, after]),
            recommendation=DynamicRecommendation("打出1号位随从"),
            dispatch=dispatch,
            token=token,
            executor_factory=lambda base: CancelAfterExecuteExecutor(
                base._dispatch_port,
                base._state_source,
                base._journal,
                base.ledger,
                base._validator,
                token=token,
            ),
        )
        result = use_case.execute(PlayTurnCommand("turn-cancel-after", 2, 10.0))
        self.assertIs(PlayTurnStatus.CANCELLED, result.status)
        self.assertEqual("cancelled_after_confirmation", result.reason_code)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual(1, len(result.action_records))

        end_token = StopToken()
        end_dispatch = ActionDispatchFake([DispatchResult.sent("end")])
        end_use_case, _, _, end_journal = self.make_use_case(
            state=ScriptedState([
                game_snapshot(),
                game_snapshot(snapshot_id="turn-snap-2", revision=8, is_my_turn=False),
            ]),
            recommendation=DynamicRecommendation("结束回合"),
            dispatch=end_dispatch,
            token=end_token,
            executor_factory=lambda base: CancelAfterExecuteExecutor(
                base._dispatch_port,
                base._state_source,
                base._journal,
                base.ledger,
                base._validator,
                token=end_token,
            ),
        )
        end_result = end_use_case.execute(PlayTurnCommand("turn-cancel-end", 2, 10.0))
        self.assertIs(PlayTurnStatus.CANCELLED, end_result.status)
        self.assertEqual("cancelled_after_confirmation", end_result.reason_code)
        self.assertFalse(end_result.ended_turn)
        self.assertEqual(1, len(end_dispatch.calls))
        self.assertEqual(1, len(end_journal.records))

    def test_bootstrap_wires_turn_use_case_without_legacy_imports(self) -> None:
        before = game_snapshot()
        after = game_snapshot(snapshot_id="turn-snap-2", revision=8, is_my_turn=False)
        state = ScriptedState([before, after])
        dispatch = ActionDispatchFake([DispatchResult.sent("end")])
        catalog = CardCatalogFake([CardDefinition("card-plain")])
        use_case = build_turn_use_case(
            DynamicRecommendation("结束回合"),
            state,
            dispatch,
            RunJournalFake(),
            card_catalog=catalog,
        )

        result = use_case.execute(
            PlayTurnCommand("turn-bootstrap", max_actions=2, timeout_seconds=10.0)
        )

        self.assertIs(PlayTurnStatus.ENDED, result.status)
        self.assertEqual(1, len(dispatch.calls))
    def test_rejected_action_stops_before_a_second_dispatch(self) -> None:
        snapshots = [
            game_snapshot(snapshot_id="turn-snap-1", revision=7),
            game_snapshot(snapshot_id="turn-snap-2", revision=8),
        ]
        dispatch = ActionDispatchFake(
            [DispatchResult.rejected("not_legal"), DispatchResult.sent("must-not-run")]
        )
        use_case, _, dispatch, journal = self.make_use_case(
            state=ScriptedState(snapshots),
            recommendation=DynamicRecommendation("打出1号位随从"),
            dispatch=dispatch,
        )

        result = use_case.execute(PlayTurnCommand("turn-rejected", 2, 10.0))

        self.assertIs(PlayTurnStatus.REJECTED, result.status)
        self.assertEqual("not_legal", result.reason_code)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual(1, len(journal.records))
        self.assertEqual(1, len(result.action_records))

    def test_confirmed_action_stops_when_post_snapshot_shows_local_turn_ended(self) -> None:
        before = game_snapshot()
        after = game_snapshot(snapshot_id="turn-snap-2", revision=8, is_my_turn=False)
        dispatch = ActionDispatchFake([DispatchResult.sent("play")])
        use_case, _, dispatch, journal = self.make_use_case(
            state=ScriptedState([before, after]),
            recommendation=DynamicRecommendation("打出1号位随从"),
            dispatch=dispatch,
        )

        result = use_case.execute(PlayTurnCommand("turn-loss", 2, 10.0))

        self.assertIs(PlayTurnStatus.ENDED, result.status)
        self.assertEqual("local_turn_ended", result.reason_code)
        self.assertFalse(result.ended_turn)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual(1, len(journal.records))

    def test_target_and_destination_are_preserved_in_dispatched_step(self) -> None:
        before = game_snapshot(hand_card="card-target")
        after = game_snapshot(snapshot_id="turn-snap-2", revision=8, hand_card="card-target")
        dispatch = ActionDispatchFake([DispatchResult.sent("play")])
        use_case, _, dispatch, _ = self.make_use_case(
            state=ScriptedState([before, after]),
            recommendation=DynamicRecommendation(
                "打出1号位随从\n目标是我方1号位随从\n放置于我方2号位"
            ),
            dispatch=dispatch,
        )

        result = use_case.execute(PlayTurnCommand("turn-target", 1, 10.0))

        self.assertIs(PlayTurnStatus.COMPLETED, result.status)
        step = dispatch.calls[0][0]
        self.assertEqual(before.my_player.board[0], step.intent.target)
        self.assertEqual(1, step.intent.destination)

    def test_total_deadline_is_checked_between_confirmed_actions(self) -> None:
        class FixedClock:
            def __init__(self) -> None:
                self.value = 0.0

            def __call__(self) -> float:
                return self.value

        class ExpiringState(ScriptedState):
            def __init__(self, snapshots, clock: FixedClock) -> None:
                super().__init__(snapshots)
                self._clock = clock
                self.current_calls = 0

            def current(self):
                observation = super().current()
                self.current_calls += 1
                if self.current_calls >= 4:
                    self._clock.value = 2.0
                return observation

        clock = FixedClock()
        snapshots = [
            game_snapshot(snapshot_id="turn-snap-1", revision=7),
            game_snapshot(snapshot_id="turn-snap-2", revision=8),
            game_snapshot(snapshot_id="turn-snap-3", revision=9),
        ]
        dispatch = ActionDispatchFake(
            [DispatchResult.sent("play-1"), DispatchResult.sent("must-not-run")]
        )
        use_case, _, dispatch, journal = self.make_use_case(
            state=ExpiringState(snapshots, clock),
            recommendation=DynamicRecommendation("打出1号位随从"),
            dispatch=dispatch,
            clock=clock,
        )

        result = use_case.execute(PlayTurnCommand("turn-expire", 2, 1.0))

        self.assertIs(PlayTurnStatus.FAILED, result.status)
        self.assertEqual("turn_timeout", result.reason_code)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual(1, len(journal.records))
        self.assertEqual(1, len(result.action_records))
    def test_default_plugin_ambiguity_fails_closed_without_dispatch(self) -> None:
        before = game_snapshot(hand_card="card-ambiguous")
        dispatch = ActionDispatchFake([DispatchResult.sent("must-not-run")])
        use_case, builder, dispatch, journal = self.make_use_case(
            state=ScriptedState([before]),
            recommendation=DynamicRecommendation(
                "打出1号位地标\n目标是敌方英雄"
            ),
            dispatch=dispatch,
        )

        result = use_case.execute(PlayTurnCommand("turn-conflict", 2, 10.0))

        self.assertIs(PlayTurnStatus.REJECTED, result.status)
        self.assertEqual("plugin_conflict", result.reason_code)
        self.assertEqual(1, builder.calls)
        self.assertEqual([], dispatch.calls)
        self.assertEqual((), journal.records)
    def test_stale_post_action_observation_stops_before_second_dispatch(self) -> None:
        before = game_snapshot()
        confirmed = game_snapshot(snapshot_id="turn-snap-2", revision=8)
        state = StaleAfterState([before, confirmed])
        dispatch = ActionDispatchFake(
            [DispatchResult.sent("first"), DispatchResult.sent("must-not-run")]
        )
        use_case, _, dispatch, journal = self.make_use_case(
            state=state,
            recommendation=DynamicRecommendation("打出1号位随从"),
            dispatch=dispatch,
        )

        result = use_case.execute(PlayTurnCommand("turn-stale-after", 2, 10.0))

        self.assertIs(PlayTurnStatus.FAILED, result.status)
        self.assertEqual("stale_post_action_state", result.reason_code)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual(1, len(journal.records))

    def test_default_plugins_route_attack_hero_power_and_location(self) -> None:
        cases = (
            (
                "attack",
                game_snapshot(),
                "操作1号位随从攻击\n目标是敌方1号位随从",
                "attack:",
            ),
            (
                "hero-power",
                game_snapshot(),
                "使用英雄技能",
                "hero-power:",
            ),
            (
                "location",
                game_snapshot(board_card="card-location"),
                "操作1号位地标",
                "location:",
            ),
        )
        for name, before, instruction, plan_prefix in cases:
            with self.subTest(name=name):
                after = game_snapshot(
                    snapshot_id=f"turn-{name}-after",
                    revision=8,
                )
                dispatch = ActionDispatchFake([DispatchResult.sent(name)])
                use_case, builder, dispatch, journal = self.make_use_case(
                    state=ScriptedState([before, after]),
                    recommendation=DynamicRecommendation(instruction),
                    dispatch=dispatch,
                )

                result = use_case.execute(
                    PlayTurnCommand(f"turn-{name}", max_actions=1, timeout_seconds=10.0)
                )

                self.assertIs(PlayTurnStatus.COMPLETED, result.status)
                self.assertEqual(1, builder.calls)
                self.assertEqual(1, len(dispatch.calls))
                self.assertEqual(1, len(journal.records))
                self.assertTrue(journal.records[0].plan_id.startswith(plan_prefix))

    def test_gate_release_failure_stops_after_confirmed_action(self) -> None:
        class FailingReleaseGate(ActionGate):
            def release(self, lease) -> None:
                raise RuntimeError("gate release failed")

        before = game_snapshot()
        after = game_snapshot(snapshot_id="turn-snap-2", revision=8, is_my_turn=False)
        use_case, _, dispatch, journal = self.make_use_case(
            state=ScriptedState([before, after]),
            recommendation=DynamicRecommendation("结束回合"),
            gate=FailingReleaseGate(),
        )

        result = use_case.execute(PlayTurnCommand("turn-release", 2, 10.0))

        self.assertIs(PlayTurnStatus.FAILED, result.status)
        self.assertEqual("gate_release_error", result.reason_code)
        self.assertEqual("RuntimeError: gate release failed", result.release_error)
        self.assertEqual(1, len(dispatch.calls))
        self.assertEqual(1, len(journal.records))
        self.assertFalse(result.ended_turn)

    def test_gate_busy_dispatches_nothing(self) -> None:
        gate = ActionGate()
        lease = gate.acquire("other", "other-command")
        try:
            use_case, _, dispatch, journal = self.make_use_case(
                state=ScriptedState([game_snapshot()]),
                recommendation=DynamicRecommendation("打出1号位随从"),
                gate=gate,
            )
            result = use_case.execute(PlayTurnCommand("turn-gate", 2, 10.0))
        finally:
            gate.release(lease)

        self.assertIs(PlayTurnStatus.GATE_BUSY, result.status)
        self.assertEqual("action_gate_busy", result.reason_code)
        self.assertEqual([], dispatch.calls)
        self.assertEqual((), journal.records)

    def test_expired_total_deadline_dispatches_nothing(self) -> None:
        class ExpiredClock:
            def __init__(self) -> None:
                self.values = [0.0, 6.0]

            def __call__(self) -> float:
                return self.values.pop(0) if self.values else 6.0

        dispatch = ActionDispatchFake([DispatchResult.sent("must-not-run")])
        use_case, _, dispatch, journal = self.make_use_case(
            state=ScriptedState([game_snapshot()]),
            recommendation=DynamicRecommendation("打出1号位随从"),
            dispatch=dispatch,
            clock=ExpiredClock(),
        )
        result = use_case.execute(
            PlayTurnCommand("turn-deadline", max_actions=2, timeout_seconds=5.0)
        )

        self.assertIs(PlayTurnStatus.FAILED, result.status)
        self.assertEqual("turn_timeout", result.reason_code)
        self.assertEqual([], dispatch.calls)
        self.assertEqual((), journal.records)


if __name__ == "__main__":
    unittest.main()
