"""Declarative action-intent dispatch over injected legacy click primitives."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from hslegend.adapters.input import LegacyActionDispatch
from hslegend.application import DispatchResult
from hslegend.domain import EntityRef, GameSnapshot, PlanStep


CardTypeResolver = Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class _PlannedClick:
    name: str
    args: tuple[Any, ...] = ()


def _normalized(value: Any) -> str:
    return str(value or "").strip().upper()


def _ordered(entities: tuple[EntityRef, ...]) -> tuple[EntityRef, ...]:
    return tuple(sorted(entities, key=lambda item: (item.zone_pos, item.entity_id)))


def _same_entity(left: EntityRef, right: EntityRef) -> bool:
    return (
        left.entity_id == right.entity_id
        and left.card_id == right.card_id
        and left.side.strip().lower() == right.side.strip().lower()
        and left.zone.strip().lower() == right.zone.strip().lower()
        and left.zone_pos == right.zone_pos
    )


def _index_of(
    entities: tuple[EntityRef, ...],
    target: EntityRef,
    *,
    side: str,
    zone: str,
) -> int | None:
    ordered = _ordered(entities)
    for index, entity in enumerate(ordered):
        if not _same_entity(entity, target):
            continue
        if (
            entity.side.strip().lower() != side
            or entity.zone.strip().lower() != zone
        ):
            return None
        return index
    return None


class LegacyClickActionDispatcher:
    """Map supported semantic actions to stable click primitives.

    Every source, target, index and post-placement adjustment is resolved
    against the pinned ``GameSnapshot`` before the first click. Invalid or
    unsupported requests return ``REJECTED`` with no click side effects. Click
    exceptions are left to ``LegacyActionDispatch`` and become ``FAILED``.
    """

    def __init__(
        self,
        click_facade: Any,
        *,
        battle_entry: Any | None = None,
        card_type_resolver: CardTypeResolver | None = None,
        dispatch_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._click = click_facade
        self._battle_entry = battle_entry
        self._card_type_resolver = card_type_resolver
        counter = itertools.count(1)
        self._adapter = LegacyActionDispatch(
            {
                "enter_traditional": self._enter_traditional,
                "select_deck": self._select_deck,
                "start_match": self._start_match,
                "mulligan_replace": self._mulligan_replace,
                "mulligan_keep": self._mulligan_keep,
                "mulligan_confirm": self._mulligan_confirm,
                "play_card": self._play_card,
                "targeted_card": self._targeted_card,
                "attack": self._attack,
                "hero_power": self._hero_power,
                "play_location": self._location,
                "location": self._location,
                "use_location": self._location,
                "end_turn": self._end_turn,
            },
            dispatch_id_factory=dispatch_id_factory
            or (lambda: f"legacy-dispatch-{next(counter)}"),
        )

    def dispatch(
        self,
        step: PlanStep,
        snapshot: GameSnapshot,
        *,
        timeout_seconds: float,
    ) -> DispatchResult:
        return self._adapter.dispatch(
            step,
            snapshot,
            timeout_seconds=timeout_seconds,
        )

    def _enter_traditional(self, step: PlanStep, snapshot: GameSnapshot):
        del step, snapshot
        ui = self._capture_ui()
        traditional = ui.get("traditional") if isinstance(ui, Mapping) else None
        if not isinstance(traditional, Mapping) or not traditional.get("visible"):
            return DispatchResult.rejected("traditional_evidence_missing")
        point = traditional.get("point")
        if (
            not isinstance(point, (list, tuple))
            or len(point) < 2
            or type(point[0]) is not int
            or type(point[1]) is not int
        ):
            return DispatchResult.rejected("traditional_evidence_missing")
        self._click_at(point[0], point[1])
        return None

    def _select_deck(self, step: PlanStep, snapshot: GameSnapshot):
        del snapshot
        if self._battle_entry is None or not callable(
            getattr(self._battle_entry, "select_deck", None)
        ):
            return DispatchResult.rejected("battle_entry_unavailable")
        index = step.intent.choice_index
        if type(index) is not int or index < 0:
            return DispatchResult.rejected("deck_index_invalid")
        try:
            raw = self._battle_entry.select_deck(
                index + 1,
                click_at_fn=self._click_at,
            )
        except Exception:
            raise
        return self._mapping_result(raw, failure_reason="select_deck_failed")

    def _start_match(self, step: PlanStep, snapshot: GameSnapshot):
        del step, snapshot
        ui = self._capture_ui()
        if not isinstance(ui, Mapping) or ui.get("state") != "deck_select":
            return DispatchResult.rejected("start_match_evidence_missing")
        self._runtime_action("match_opponent")
        return None

    def _capture_ui(self):
        if self._battle_entry is None or not callable(
            getattr(self._battle_entry, "capture_client_frame", None)
        ) or not callable(getattr(self._battle_entry, "identify", None)):
            return None
        captured = self._battle_entry.capture_client_frame()
        frame = captured[0] if isinstance(captured, tuple) else captured
        if frame is None:
            return None
        return self._battle_entry.identify(frame)

    def _click_at(self, x: int, y: int) -> Any:
        return self._runtime_action("left_click", int(x), int(y))

    def _runtime_action(self, name: str, *args: Any) -> Any:
        primitive = getattr(self._click, name, None)
        if not callable(primitive):
            raise AttributeError(f"click facade is missing {name}()")

        def action():
            return self._call(name, *args)

        runner = getattr(self._click, "run_hearthstone_action", None)
        if callable(runner):
            return runner(action)
        return action()

    @staticmethod
    def _mapping_result(raw: Any, *, failure_reason: str) -> Any:
        if isinstance(raw, DispatchResult):
            return raw
        if not isinstance(raw, Mapping):
            raise ValueError("legacy lobby action must return a mapping")
        if raw.get("ok") is True:
            return None
        if raw.get("cancelled"):
            return DispatchResult.rejected("select_deck_cancelled")
        if raw.get("skipped"):
            return DispatchResult.rejected("select_deck_skipped")
        return DispatchResult.rejected(failure_reason)

    def _mulligan_replace(self, step: PlanStep, snapshot: GameSnapshot):
        source = step.intent.source
        if source is None:
            return DispatchResult.rejected("source_required")
        hand = _ordered(snapshot.my_player.hand)
        index = step.intent.choice_index
        if index is None:
            index = _index_of(hand, source, side="friendly", zone="hand")
        if type(index) is not int or not 0 <= index < len(hand):
            return DispatchResult.rejected("mulligan_slot_invalid")
        if not _same_entity(hand[index], source):
            return DispatchResult.rejected("stale_entity")
        self._execute((_PlannedClick("replace_starting_card", (index, len(hand))),))
        return None

    def _mulligan_keep(self, step: PlanStep, snapshot: GameSnapshot):
        source = step.intent.source
        if source is not None and _index_of(
            snapshot.my_player.hand, source, side="friendly", zone="hand"
        ) is None:
            return DispatchResult.rejected("stale_entity")
        # Keeping is the absence of a replacement click.
        return None

    def _mulligan_confirm(self, step: PlanStep, snapshot: GameSnapshot):
        del step, snapshot
        self._execute((_PlannedClick("commit_choose_card"),))
        return None

    def _play_card(self, step: PlanStep, snapshot: GameSnapshot):
        return self._play(step, snapshot, target_required=False)

    def _targeted_card(self, step: PlanStep, snapshot: GameSnapshot):
        return self._play(step, snapshot, target_required=True)

    def _play(self, step: PlanStep, snapshot: GameSnapshot, *, target_required: bool):
        source = step.intent.source
        if source is None:
            return DispatchResult.rejected("source_required")
        hand = _ordered(snapshot.my_player.hand)
        hand_index = _index_of(hand, source, side="friendly", zone="hand")
        if hand_index is None:
            return DispatchResult.rejected("stale_entity")
        target = step.intent.target
        if target_required and target is None:
            return DispatchResult.rejected("target_required")
        card_type = self._card_type(step.intent.card_id or source.card_id)
        if card_type is None:
            return DispatchResult.rejected("unknown_card_type")

        if target is not None:
            if card_type in {"MINION", "LOCATION"}:
                placement, insert_at = self._minion_placement_plan(
                    step, snapshot, hand_index
                )
                target_plan = self._target_click_plan(
                    snapshot,
                    target,
                    friendly_board_delta=1,
                    insert_at=insert_at,
                )
            elif card_type in {"SPELL", "HERO"}:
                placement = (_PlannedClick("choose_card", (hand_index, len(hand))),)
                target_plan = self._target_click_plan(snapshot, target)
            else:
                return DispatchResult.rejected("unsupported_card_type")
            if target_plan is None:
                return DispatchResult.rejected("invalid_target")
            self._execute((*placement, *target_plan))
            return None

        if card_type in {"MINION", "LOCATION"}:
            placement, _insert_at = self._minion_placement_plan(
                step, snapshot, hand_index
            )
            self._execute(placement)
        elif card_type == "SPELL":
            self._execute(
                (_PlannedClick("choose_and_use_spell", (hand_index, len(hand))),)
            )
        elif card_type in {"WEAPON", "HERO"}:
            self._execute(
                (
                    _PlannedClick("choose_card", (hand_index, len(hand))),
                    _PlannedClick("click_middle"),
                )
            )
        else:
            return DispatchResult.rejected("unsupported_card_type")
        return None

    def _minion_placement_plan(
        self, step: PlanStep, snapshot: GameSnapshot, hand_index: int
    ) -> tuple[tuple[_PlannedClick, ...], int]:
        board = _ordered(snapshot.my_player.board)
        board_count = len(board)
        destination = step.intent.destination
        if destination is None:
            insert_at = board_count
        else:
            if type(destination) is not int or not 0 <= destination <= board_count:
                raise ValueError("destination is outside the pinned board")
            insert_at = destination
        hand_count = len(snapshot.my_player.hand)
        if insert_at < board_count:
            return (
                (
                    _PlannedClick(
                        "drag_card_to_board_entity",
                        (hand_index, hand_count, insert_at, board_count),
                    ),
                ),
                insert_at,
            )
        return (
            (
                _PlannedClick("choose_card", (hand_index, hand_count)),
                _PlannedClick("put_minion", (insert_at, board_count)),
            ),
            insert_at,
        )

    def _target_click_plan(
        self,
        snapshot: GameSnapshot,
        target: EntityRef,
        *,
        friendly_board_delta: int = 0,
        insert_at: int | None = None,
    ) -> tuple[_PlannedClick, ...] | None:
        side = target.side.strip().lower()
        zone = target.zone.strip().lower()
        if side == "friendly":
            board = _ordered(snapshot.my_player.board)
            if zone == "board":
                index = _index_of(board, target, side="friendly", zone="board")
                if index is None:
                    return None
                if friendly_board_delta and insert_at is not None and insert_at <= index:
                    index += friendly_board_delta
                return (
                    _PlannedClick(
                        "choose_my_minion",
                        (index, len(board) + friendly_board_delta),
                    ),
                )
            if zone == "hero":
                hero = snapshot.my_player.hero
                if hero is None or not _same_entity(hero, target):
                    return None
                return (_PlannedClick("choose_my_hero"),)
            return None
        if side == "opponent":
            board = _ordered(snapshot.opponent.board)
            if zone == "board":
                index = _index_of(board, target, side="opponent", zone="board")
                if index is None:
                    return None
                return (_PlannedClick("choose_opponent_minion", (index, len(board))),)
            if zone == "hero":
                hero = snapshot.opponent.hero
                if hero is None or not _same_entity(hero, target):
                    return None
                return (_PlannedClick("choose_oppo_hero"),)
        return None

    def _attack(self, step: PlanStep, snapshot: GameSnapshot):
        source = step.intent.source
        target = step.intent.target
        if source is None or target is None:
            return DispatchResult.rejected("source_and_target_required")
        source_is_hero = source.side.strip().lower() == "friendly" and (
            source.zone.strip().lower() == "hero"
        )
        target_is_hero = target.side.strip().lower() == "opponent" and (
            target.zone.strip().lower() == "hero"
        )
        friendly_board = _ordered(snapshot.my_player.board)
        opponent_board = _ordered(snapshot.opponent.board)

        if source_is_hero:
            hero = snapshot.my_player.hero
            if hero is None or not _same_entity(hero, source):
                return DispatchResult.rejected("stale_entity")
            source_index = None
        else:
            source_index = _index_of(
                friendly_board, source, side="friendly", zone="board"
            )
            if source_index is None:
                return DispatchResult.rejected("stale_entity")
        if target_is_hero:
            hero = snapshot.opponent.hero
            if hero is None or not _same_entity(hero, target):
                return DispatchResult.rejected("stale_entity")
            target_index = None
        else:
            target_index = _index_of(
                opponent_board, target, side="opponent", zone="board"
            )
            if target_index is None:
                return DispatchResult.rejected("stale_entity")

        if source_is_hero and target_is_hero:
            plan = (_PlannedClick("hero_beat_hero"),)
        elif source_is_hero:
            plan = (
                _PlannedClick(
                    "hero_beat_minion",
                    (target_index, len(opponent_board)),
                ),
            )
        elif target_is_hero:
            plan = (
                _PlannedClick(
                    "minion_beat_hero",
                    (source_index, len(friendly_board)),
                ),
            )
        else:
            plan = (
                _PlannedClick(
                    "minion_beat_minion",
                    (
                        source_index,
                        len(friendly_board),
                        target_index,
                        len(opponent_board),
                    ),
                ),
            )
        self._execute(plan)
        return None

    def _hero_power(self, step: PlanStep, snapshot: GameSnapshot):
        source = step.intent.source
        if source is not None:
            hero = snapshot.my_player.hero
            if hero is None or not _same_entity(hero, source):
                return DispatchResult.rejected("stale_entity")
        target = step.intent.target
        if target is None:
            self._execute((_PlannedClick("use_skill_no_point"),))
            return None

        side = target.side.strip().lower()
        if side == "friendly":
            target_plan = self._target_click_plan(snapshot, target)
            if target_plan is None:
                return DispatchResult.rejected("invalid_target")
            target_click = target_plan[0]
            board = _ordered(snapshot.my_player.board)
            index = -1
            if target_click.name == "choose_my_minion":
                index = target_click.args[0]
            elif target_click.name != "choose_my_hero":
                return DispatchResult.rejected("invalid_target")
            plan = (_PlannedClick("use_skill_point_mine", (index, len(board))),)
        elif side == "opponent":
            target_plan = self._target_click_plan(snapshot, target)
            if target_plan is None:
                return DispatchResult.rejected("invalid_target")
            plan = (
                _PlannedClick("click_skill"),
                *target_plan,
                _PlannedClick("cancel_click"),
            )
        else:
            return DispatchResult.rejected("invalid_target")
        self._execute(plan)
        return None

    def _location(self, step: PlanStep, snapshot: GameSnapshot):
        source = step.intent.source
        if source is None:
            return DispatchResult.rejected("source_required")
        board = _ordered(snapshot.my_player.board)
        index = _index_of(board, source, side="friendly", zone="board")
        if index is None:
            return DispatchResult.rejected("stale_entity")
        target_plan: tuple[_PlannedClick, ...] = ()
        if step.intent.target is not None:
            resolved = self._target_click_plan(snapshot, step.intent.target)
            if resolved is None:
                return DispatchResult.rejected("invalid_target")
            target_plan = (*resolved, _PlannedClick("cancel_click"))
        self._execute(
            (
                _PlannedClick("choose_my_board_entity", (index, len(board))),
                *target_plan,
            )
        )
        return None

    def _end_turn(self, step: PlanStep, snapshot: GameSnapshot):
        del step, snapshot
        self._execute((_PlannedClick("end_turn"),))
        return None

    def _card_type(self, card_id: str) -> str | None:
        if self._card_type_resolver is None:
            return None
        value = self._card_type_resolver(card_id)
        if value is None:
            return None
        normalized = _normalized(value)
        return normalized or None

    def _execute(self, clicks: tuple[_PlannedClick, ...]) -> None:
        for click in clicks:
            self._call(click.name, *click.args)

    def _call(self, name: str, *args: Any) -> Any:
        primitive = getattr(self._click, name, None)
        if not callable(primitive):
            raise AttributeError(f"click facade is missing {name}()")
        result = primitive(*args)
        if result is False:
            raise RuntimeError(f"click primitive {name} rejected the action")
        if isinstance(result, Mapping) and result.get("ok") is False:
            detail = result.get("error") or result.get("reason") or "rejected"
            raise RuntimeError(f"click primitive {name} rejected: {detail}")
        return result


__all__ = ["LegacyClickActionDispatcher"]
