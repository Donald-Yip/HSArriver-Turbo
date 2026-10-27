"""Exact click-call tests for the legacy semantic action bridge."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from hslegend.adapters.legacy import LegacyClickActionDispatcher
from hslegend.application import DispatchStatus
from hslegend.domain import ActionIntent, EntityRef, PlanStep
from tests.domain.support import (
    FRIENDLY_BOARD,
    FRIENDLY_HAND,
    OPPONENT_BOARD,
    snapshot,
)


class FakeClicks:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def __getattr__(self, name: str):
        def record(*args):
            self.calls.append((name, args))
            return None

        return record


def step(action_type: str, **intent_kwargs) -> PlanStep:
    return PlanStep(
        step_id="step-1",
        intent=ActionIntent(action_type=action_type, intent_id="intent-1", **intent_kwargs),
    )


def dispatch(dispatcher, plan_step, current=None):
    return dispatcher.dispatch(plan_step, current or snapshot(), timeout_seconds=1.0)


class _RuntimeClicks:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.runs = 0

    def run_hearthstone_action(self, action):
        self.runs += 1
        return action()

    def left_click(self, x, y):
        self.calls.append(("left_click", (x, y)))

    def match_opponent(self):
        self.calls.append(("match_opponent", ()))

    def end_turn(self):
        self.calls.append(("end_turn", ()))


class _FakeBattleEntry:
    def __init__(self, ui, select_result=None) -> None:
        self.ui = ui
        self.select_result = select_result or {"ok": True}
        self.select_calls: list[tuple[int, object]] = []

    def capture_client_frame(self):
        return ("frame", {"mapping": True})

    def identify(self, frame):
        del frame
        return self.ui

    def select_deck(self, index, *, click_at_fn):
        self.select_calls.append((index, click_at_fn))
        if self.select_result.get("ok") is True:
            click_at_fn(100, 200)
        return self.select_result


class NativeLobbyDispatchTests(unittest.TestCase):
    def test_native_enter_select_start_and_in_game_binding(self) -> None:
        clicks = _RuntimeClicks()
        battle = _FakeBattleEntry(
            {
                "state": "deck_select",
                "traditional": {"visible": True, "point": [300, 400]},
            }
        )
        dispatcher = LegacyClickActionDispatcher(clicks, battle_entry=battle)

        enter = dispatch(dispatcher, step("enter_traditional"))
        select = dispatch(
            dispatcher,
            step("select_deck", choice_index=2),
            snapshot(),
        )
        start = dispatch(dispatcher, step("start_match"))
        in_game = dispatch(dispatcher, step("end_turn"))

        self.assertIs(DispatchStatus.SENT, enter.status)
        self.assertIs(DispatchStatus.SENT, select.status)
        self.assertIs(DispatchStatus.SENT, start.status)
        self.assertIs(DispatchStatus.SENT, in_game.status)
        self.assertEqual(1, len(battle.select_calls))
        self.assertEqual(3, battle.select_calls[0][0])
        self.assertEqual(
            [
                ("left_click", (300, 400)),
                ("left_click", (100, 200)),
                ("match_opponent", ()),
                ("end_turn", ()),
            ],
            clicks.calls,
        )

    def test_missing_traditional_or_start_evidence_clicks_nothing(self) -> None:
        for action_type, ui in (
            ("enter_traditional", {"state": "unknown", "traditional": {"visible": False}}),
            ("start_match", {"state": "unknown"}),
        ):
            with self.subTest(action_type=action_type):
                clicks = _RuntimeClicks()
                battle = _FakeBattleEntry(ui)
                dispatcher = LegacyClickActionDispatcher(clicks, battle_entry=battle)
                result = dispatch(dispatcher, step(action_type))
                self.assertIs(DispatchStatus.REJECTED, result.status)
                self.assertEqual([], clicks.calls)
                self.assertEqual([], battle.select_calls)

    def test_select_deck_mapping_failure_is_typed_and_clicks_nothing(self) -> None:
        clicks = _RuntimeClicks()
        battle = _FakeBattleEntry(
            {"state": "deck_select"},
            select_result={"ok": False, "skipped": True, "error": "not a deck"},
        )
        dispatcher = LegacyClickActionDispatcher(clicks, battle_entry=battle)
        result = dispatch(dispatcher, step("select_deck", choice_index=0))
        self.assertIs(DispatchStatus.REJECTED, result.status)
        self.assertEqual("select_deck_skipped", result.reason_code)
        self.assertEqual([], clicks.calls)

    def test_preloaded_click_module_is_not_used_by_lobby_or_game_paths(self) -> None:
        external_calls: list[tuple[int, int]] = []
        external_click = SimpleNamespace(
            left_click=lambda x, y: external_calls.append((x, y)),
            match_opponent=lambda: external_calls.append((-1, -1)),
        )
        clicks = _RuntimeClicks()
        battle = _FakeBattleEntry(
            {
                "state": "deck_select",
                "traditional": {"visible": True, "point": [50, 60]},
            }
        )
        dispatcher = LegacyClickActionDispatcher(clicks, battle_entry=battle)

        with patch.dict(sys.modules, {"click": external_click}, clear=False):
            results = (
                dispatch(dispatcher, step("enter_traditional")),
                dispatch(dispatcher, step("select_deck", choice_index=0)),
                dispatch(dispatcher, step("start_match")),
                dispatch(dispatcher, step("end_turn")),
            )
            self.assertIs(external_click, sys.modules["click"])

        self.assertTrue(all(result.status is DispatchStatus.SENT for result in results))
        self.assertEqual([], external_calls)
        self.assertEqual(
            [
                ("left_click", (50, 60)),
                ("left_click", (100, 200)),
                ("match_opponent", ()),
                ("end_turn", ()),
            ],
            clicks.calls,
        )


class LegacyClickActionDispatcherTests(unittest.TestCase):
    def test_mulligan_replace_and_confirm(self) -> None:
        clicks = FakeClicks()
        dispatcher = LegacyClickActionDispatcher(clicks)

        replace = dispatch(
            dispatcher,
            step(
                "mulligan_replace",
                source=FRIENDLY_HAND,
                choice_index=0,
            ),
        )
        confirm = dispatch(dispatcher, step("mulligan_confirm"))

        self.assertIs(DispatchStatus.SENT, replace.status)
        self.assertIs(DispatchStatus.SENT, confirm.status)
        self.assertEqual(
            [
                ("replace_starting_card", (0, 1)),
                ("commit_choose_card", ()),
            ],
            clicks.calls,
        )

    def test_mulligan_keep_does_not_click(self) -> None:
        clicks = FakeClicks()
        dispatcher = LegacyClickActionDispatcher(clicks)
        result = dispatch(dispatcher, step("mulligan_keep", source=FRIENDLY_HAND))
        self.assertIs(DispatchStatus.SENT, result.status)
        self.assertEqual([], clicks.calls)

    def test_play_minion_uses_hand_and_board_counts(self) -> None:
        clicks = FakeClicks()
        dispatcher = LegacyClickActionDispatcher(
            clicks,
            card_type_resolver=lambda card_id: "MINION",
        )

        result = dispatch(dispatcher, step("play_card", source=FRIENDLY_HAND))

        self.assertIs(DispatchStatus.SENT, result.status)
        self.assertEqual(
            [
                ("choose_card", (0, 1)),
                ("put_minion", (1, 1)),
            ],
            clicks.calls,
        )

    def test_targeted_card_clicks_hand_then_opponent_board_target(self) -> None:
        clicks = FakeClicks()
        dispatcher = LegacyClickActionDispatcher(
            clicks,
            card_type_resolver=lambda card_id: "SPELL",
        )

        result = dispatch(
            dispatcher,
            step(
                "targeted_card",
                source=FRIENDLY_HAND,
                target=OPPONENT_BOARD,
            ),
        )

        self.assertIs(DispatchStatus.SENT, result.status)
        self.assertEqual(
            [
                ("choose_card", (0, 1)),
                ("choose_opponent_minion", (0, 1)),
            ],
            clicks.calls,
        )

    def test_attack_maps_to_exact_minion_primitive(self) -> None:
        clicks = FakeClicks()
        dispatcher = LegacyClickActionDispatcher(clicks)

        result = dispatch(
            dispatcher,
            step("attack", source=FRIENDLY_BOARD, target=OPPONENT_BOARD),
        )

        self.assertIs(DispatchStatus.SENT, result.status)
        self.assertEqual(
            [("minion_beat_minion", (0, 1, 0, 1))],
            clicks.calls,
        )

    def test_hero_power_without_and_with_target(self) -> None:
        clicks = FakeClicks()
        dispatcher = LegacyClickActionDispatcher(clicks)
        current = snapshot()

        no_target = dispatch(dispatcher, step("hero_power"), current)
        with_target = dispatch(
            dispatcher,
            step("hero_power", target=current.my_player.hero),
            current,
        )

        self.assertIs(DispatchStatus.SENT, no_target.status)
        self.assertIs(DispatchStatus.SENT, with_target.status)
        self.assertEqual(
            [
                ("use_skill_no_point", ()),
                ("use_skill_point_mine", (-1, 1)),
            ],
            clicks.calls,
        )

    def test_location_family_uses_board_position(self) -> None:
        for action_type in ("play_location", "location", "use_location"):
            with self.subTest(action_type=action_type):
                clicks = FakeClicks()
                dispatcher = LegacyClickActionDispatcher(clicks)
                result = dispatch(dispatcher, step(action_type, source=FRIENDLY_BOARD))
                self.assertIs(DispatchStatus.SENT, result.status)
                self.assertEqual(
                    [("choose_my_board_entity", (0, 1))],
                    clicks.calls,
                )

    def test_end_turn_uses_one_primitive(self) -> None:
        clicks = FakeClicks()
        dispatcher = LegacyClickActionDispatcher(clicks)
        result = dispatch(dispatcher, step("end_turn"))
        self.assertIs(DispatchStatus.SENT, result.status)
        self.assertEqual([("end_turn", ())], clicks.calls)

    def test_unsupported_action_is_rejected_without_clicks(self) -> None:
        clicks = FakeClicks()
        dispatcher = LegacyClickActionDispatcher(clicks)
        result = dispatch(dispatcher, step("concede"))
        self.assertIs(DispatchStatus.REJECTED, result.status)
        self.assertEqual("unknown_action", result.reason_code)
        self.assertEqual([], clicks.calls)

    def test_stale_entity_is_rejected_without_clicks(self) -> None:
        clicks = FakeClicks()
        dispatcher = LegacyClickActionDispatcher(
            clicks,
            card_type_resolver=lambda card_id: "MINION",
        )
        stale = FRIENDLY_HAND.__class__(999, "gone", "friendly", "hand", 1)
        result = dispatch(dispatcher, step("play_card", source=stale))
        self.assertIs(DispatchStatus.REJECTED, result.status)
        self.assertEqual("stale_entity", result.reason_code)
        self.assertEqual([], clicks.calls)

    def test_invalid_targets_reject_before_any_click(self) -> None:
        current = snapshot()
        stale = EntityRef(999, "gone", "opponent", "board", 1)
        cases = (
            (
                "targeted_card",
                dict(source=FRIENDLY_HAND, target=stale),
                "SPELL",
            ),
            (
                "play_card",
                dict(source=FRIENDLY_HAND, target=stale),
                "MINION",
            ),
            (
                "hero_power",
                dict(target=stale),
                None,
            ),
            (
                "location",
                dict(source=FRIENDLY_BOARD, target=stale),
                None,
            ),
        )
        for action_type, kwargs, card_type in cases:
            with self.subTest(action_type=action_type):
                clicks = FakeClicks()
                dispatcher = LegacyClickActionDispatcher(
                    clicks,
                    card_type_resolver=(lambda _card_id, value=card_type: value),
                )
                result = dispatch(dispatcher, step(action_type, **kwargs), current)
                self.assertIs(DispatchStatus.REJECTED, result.status)
                self.assertEqual("invalid_target", result.reason_code)
                self.assertEqual([], clicks.calls)

    def test_minion_target_index_adjusts_after_placement(self) -> None:
        first = EntityRef(101, "board-a", "friendly", "board", 1)
        second = EntityRef(102, "board-b", "friendly", "board", 2)
        base = snapshot()
        current = replace(
            base,
            my_player=replace(base.my_player, board=(first, second)),
        )
        clicks = FakeClicks()
        dispatcher = LegacyClickActionDispatcher(
            clicks,
            card_type_resolver=lambda card_id: "MINION",
        )

        result = dispatch(
            dispatcher,
            step(
                "play_card",
                source=FRIENDLY_HAND,
                target=second,
                destination=0,
            ),
            current,
        )

        self.assertIs(DispatchStatus.SENT, result.status)
        self.assertEqual(
            [
                ("drag_card_to_board_entity", (0, 1, 0, 2)),
                ("choose_my_minion", (2, 3)),
            ],
            clicks.calls,
        )

    def test_hero_power_opponent_target_validates_before_clicking(self) -> None:
        current = snapshot()
        clicks = FakeClicks()
        dispatcher = LegacyClickActionDispatcher(clicks)

        result = dispatch(
            dispatcher,
            step("hero_power", target=OPPONENT_BOARD),
            current,
        )

        self.assertIs(DispatchStatus.SENT, result.status)
        self.assertEqual(
            [
                ("click_skill", ()),
                ("choose_opponent_minion", (0, 1)),
                ("cancel_click", ()),
            ],
            clicks.calls,
        )

    def test_click_exception_becomes_failed_never_success(self) -> None:
        class RaisingClicks(FakeClicks):
            def __getattr__(self, name: str):
                def raise_error(*args):
                    del args
                    raise RuntimeError("input backend unavailable")

                return raise_error

        dispatcher = LegacyClickActionDispatcher(RaisingClicks())
        result = dispatch(dispatcher, step("end_turn"))
        self.assertIs(DispatchStatus.FAILED, result.status)
        self.assertEqual("handler_exception", result.reason_code)


if __name__ == "__main__":
    unittest.main()
