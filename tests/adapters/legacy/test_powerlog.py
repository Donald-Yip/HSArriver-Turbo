"""Synthetic Power.log mapping and revision-wait tests."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from log_op import LOG_CONTAINER_ERROR, LOG_CONTAINER_INFO
from log_state import CardEntity, GameEntity, LogState, PlayerEntity

from hslegend.adapters.legacy import (
    LegacyLogStateSnapshotMapper,
    LegacyPowerLogError,
    LegacyPowerLogPump,
)
from hslegend.adapters.powerlog import PowerLogGameStateAdapter


def _container(messages=(), session_id: str = "session-1"):
    return SimpleNamespace(
        log_type=LOG_CONTAINER_INFO,
        session_id=session_id,
        message_list=list(messages),
    )


def _card(
    state: LogState,
    entity_id: str,
    card_id: str,
    controller: str,
    cardtype: str,
    zone: str,
    zone_pos: int,
    *,
    health: int = 30,
    damage: int = 0,
    armor: int = 0,
) -> CardEntity:
    card = CardEntity(card_id)
    card.set_tag("CONTROLLER", controller)
    card.set_tag("CARDTYPE", cardtype)
    card.set_tag("ZONE", zone)
    if zone != "PLAY" or cardtype != "HERO":
        card.set_tag("ZONE_POSITION", str(zone_pos))
    if cardtype == "HERO":
        card.set_tag("HEALTH", str(health))
        card.set_tag("DAMAGE", str(damage))
        card.set_tag("ARMOR", str(armor))
    state.add_entity(entity_id, card)
    return card


def synthetic_state(*, step: str = "MAIN_READY", complete: bool = False) -> LogState:
    state = LogState()
    state.session_id = "session-1"
    state.revision = 12
    state.game_generation = 3
    state.player_id_map_dict = {"1": "2", "2": "3"}
    state.my_player_id = "1"
    state.oppo_player_id = "2"
    state.my_name = "Friendly#1"
    state.oppo_name = "Opponent#2"
    state.player_names = {"1": "Friendly#1", "2": "Opponent#2"}

    game = GameEntity()
    game.set_tag("STATE", "COMPLETE" if complete else "RUNNING")
    game.set_tag("STEP", step)
    game.set_tag("NUM_TURNS_IN_PLAY", "4")
    state.set_game_entity("1", game)

    friendly = PlayerEntity()
    friendly.set_tag("RESOURCES", "6")
    friendly.set_tag("RESOURCES_USED", "2")
    friendly.set_tag("CURRENT_PLAYER", "1")
    state.add_player_entity("2", "1", friendly)
    opponent = PlayerEntity()
    opponent.set_tag("RESOURCES", "5")
    opponent.set_tag("RESOURCES_USED", "1")
    opponent.set_tag("CURRENT_PLAYER", "0")
    state.add_player_entity("3", "2", opponent)

    _card(state, "10", "HERO_F", "1", "HERO", "PLAY", 0, health=30, damage=3, armor=1)
    _card(state, "11", "HERO_O", "2", "HERO", "PLAY", 0, health=28, damage=2)
    _card(state, "30", "MINION_A", "1", "MINION", "PLAY", 2)
    _card(state, "32", "MINION_B", "1", "MINION", "PLAY", 1)
    _card(state, "40", "HAND_A", "1", "MINION", "HAND", 1)
    _card(state, "41", "HAND_B", "1", "SPELL", "HAND", 2)
    _card(state, "50", "DECK_A", "1", "MINION", "DECK", 1)
    _card(state, "60", "WEAPON_A", "1", "WEAPON", "PLAY", 0)
    _card(state, "70", "SECRET_A", "1", "SPELL", "SECRET", 1)
    _card(state, "80", "OPP_MINION", "2", "MINION", "PLAY", 1)

    if step == "BEGIN_MULLIGAN":
        state.general_choice_id = "mulligan-choice"
        state.general_choice_player = "1"
        state.general_choice_indexes = {1}
        state.general_choice_ready = True
    else:
        state.general_choice_id = "choice-1"
        state.general_choice_player = "1"
        state.general_choice_indexes = {1}
        state.general_choice_entity_ids = {1: "90"}
        state.general_choice_cards = {1: "CHOICE_A"}
        state.general_choice_ready = True
    return state


class SnapshotMappingTests(unittest.TestCase):
    def test_maps_revision_generation_players_zones_and_choices(self) -> None:
        state = synthetic_state()
        pump = LegacyPowerLogPump(state=state, containers=[_container()], clock=lambda: 5.0)
        mapper = LegacyLogStateSnapshotMapper(pump, clock=lambda: 5.0)

        snapshot = mapper.current()

        self.assertEqual("powerlog:session-1:3:12", snapshot.snapshot_id)
        self.assertEqual(12, snapshot.log_revision)
        self.assertEqual(3, snapshot.game_generation)
        self.assertEqual(4, snapshot.turn_number)
        self.assertTrue(snapshot.is_my_turn)
        self.assertFalse(snapshot.is_end)
        self.assertEqual((4, 6, 27, 1), (
            snapshot.my_player.mana,
            snapshot.my_player.max_mana,
            snapshot.my_player.health,
            snapshot.my_player.armor,
        ))
        self.assertEqual((32, 30), tuple(e.entity_id for e in snapshot.my_player.board))
        self.assertEqual((40, 41), tuple(e.entity_id for e in snapshot.my_player.hand))
        self.assertEqual("weapon", snapshot.my_player.weapon.zone)
        self.assertEqual((70,), tuple(e.entity_id for e in snapshot.my_player.secrets))
        self.assertEqual("discover", snapshot.choices[0].choice_type)
        self.assertEqual((90,), tuple(e.entity_id for e in snapshot.choices[0].options))

    def test_mulligan_step_is_exposed_as_choice_surface(self) -> None:
        state = synthetic_state(step="BEGIN_MULLIGAN")
        pump = LegacyPowerLogPump(state=state, containers=[_container()], clock=lambda: 5.0)
        snapshot = LegacyLogStateSnapshotMapper(pump, clock=lambda: 5.0).current()

        self.assertEqual(("mulligan",), tuple(c.choice_type for c in snapshot.choices))
        self.assertEqual((), snapshot.choices[0].options)

    def test_complete_state_sets_is_end(self) -> None:
        state = synthetic_state(complete=True)
        pump = LegacyPowerLogPump(state=state, containers=[_container()], clock=lambda: 5.0)
        snapshot = LegacyLogStateSnapshotMapper(pump, clock=lambda: 5.0).current()
        self.assertTrue(snapshot.is_end)

    def test_current_raises_on_container_error_and_recovers(self) -> None:
        state = synthetic_state()
        error = SimpleNamespace(
            log_type=LOG_CONTAINER_ERROR,
            session_id="session-1",
            message_list=[],
        )
        pump = LegacyPowerLogPump(
            state=state,
            containers=[error, _container()],
            clock=lambda: 5.0,
        )
        mapper = LegacyLogStateSnapshotMapper(pump, clock=lambda: 5.0)

        with self.assertRaises(LegacyPowerLogError) as raised:
            mapper.current()
        self.assertEqual("power_log_unavailable", raised.exception.reason_code)

        snapshot = mapper.current()
        self.assertEqual("session-1", snapshot.snapshot_id.split(":")[1])
        self.assertEqual(12, snapshot.log_revision)

    def test_revision_raises_on_container_error_and_recovers(self) -> None:
        state = synthetic_state()
        error = SimpleNamespace(
            log_type=LOG_CONTAINER_ERROR,
            session_id="session-1",
            message_list=[],
        )
        pump = LegacyPowerLogPump(
            state=state,
            containers=[error, _container()],
            clock=lambda: 5.0,
        )
        mapper = LegacyLogStateSnapshotMapper(pump, clock=lambda: 5.0)

        with self.assertRaises(LegacyPowerLogError) as raised:
            mapper.revision()
        self.assertEqual("power_log_unavailable", raised.exception.reason_code)

        self.assertEqual(12, mapper.revision())

    def test_log_container_error_returns_current_state_without_mutation_error(self) -> None:
        state = LogState()
        container = SimpleNamespace(
            log_type=LOG_CONTAINER_ERROR,
            session_id="session-1",
            message_list=[],
        )
        pump = LegacyPowerLogPump(
            state=state,
            containers=[container],
            clock=lambda: 5.0,
        )

        returned = pump.pump()

        self.assertIs(state, returned)
        self.assertEqual("power_log_unavailable", pump.last_error)

    def test_incomplete_general_choice_is_omitted(self) -> None:
        state = synthetic_state()
        state.general_choice_ready = False
        pump = LegacyPowerLogPump(state=state, containers=[_container()], clock=lambda: 5.0)
        snapshot = LegacyLogStateSnapshotMapper(pump, clock=lambda: 5.0).current()
        self.assertEqual((), snapshot.choices)

    def test_opponent_owned_general_choice_is_omitted(self) -> None:
        state = synthetic_state()
        state.general_choice_player = state.oppo_player_id
        pump = LegacyPowerLogPump(state=state, containers=[_container()], clock=lambda: 5.0)
        snapshot = LegacyLogStateSnapshotMapper(pump, clock=lambda: 5.0).current()
        self.assertEqual((), snapshot.choices)

    def test_malformed_choice_index_is_typed(self) -> None:
        state = synthetic_state()
        state.general_choice_indexes = {"not-an-int"}
        pump = LegacyPowerLogPump(state=state, containers=[_container()], clock=lambda: 5.0)
        with self.assertRaises(LegacyPowerLogError) as raised:
            LegacyLogStateSnapshotMapper(pump, clock=lambda: 5.0).current()
        self.assertEqual("invalid_choice_index", raised.exception.reason_code)

    def test_missing_ownership_fails_closed(self) -> None:
        state = synthetic_state()
        state.entity_dict["30"].tag_dict.pop("CONTROLLER")
        pump = LegacyPowerLogPump(state=state, containers=[_container()], clock=lambda: 5.0)
        with self.assertRaises(LegacyPowerLogError) as raised:
            LegacyLogStateSnapshotMapper(pump, clock=lambda: 5.0).current()
        self.assertEqual("missing_entity_ownership", raised.exception.reason_code)

    def test_scripted_pump_advances_revision_and_wait_observes_later_revision(self) -> None:
        state = synthetic_state()
        state.revision = 0

        def updater(current, amount):
            current.revision += amount
            return True

        containers = [_container([1]), _container([1])]
        pump = LegacyPowerLogPump(
            containers=containers,
            state=state,
            updater=updater,
            clock=lambda: 5.0,
        )
        mapper = LegacyLogStateSnapshotMapper(pump, clock=lambda: 5.0)
        adapter = PowerLogGameStateAdapter(
            mapper.current,
            mapper.revision,
            clock=lambda: 0.0,
            sleep_func=lambda _seconds: None,
            stop_token=None,
        )

        observation = adapter.wait_for_revision_change(0, 1.0)

        self.assertIsNotNone(observation)
        assert observation is not None
        self.assertEqual(2, observation.snapshot.log_revision)


if __name__ == "__main__":
    unittest.main()
