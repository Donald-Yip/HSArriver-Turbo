import unittest

from constants.constants import (
    LOG_LINE_FULL_ENTITY,
    LOG_LINE_FULL_ENTITY_PLAYER,
    LOG_LINE_TAG,
    LOG_LINE_TAG_CHANGE,
)
from log_op import LineInfoContainer, parse_line
from log_state import CardEntity, LogState, PlayerEntity, update_state


class PowerLogHandEntryTests(unittest.TestCase):
    @staticmethod
    def _card(card_id, controller, zone):
        card = CardEntity(card_id)
        card.set_tag("CONTROLLER", controller)
        card.set_tag("ZONE", zone)
        return card

    @staticmethod
    def _zone_change(entity_id, value):
        return LineInfoContainer(
            LOG_LINE_TAG_CHANGE,
            entity=entity_id,
            tag="ZONE",
            value=value,
        )

    def test_counts_only_friendly_hand_entries(self):
        def transitions(*zones):
            state = LogState()
            state.my_player_id = "1"
            state.add_entity("10", self._card("TEST_001", "1", "DECK"))
            for zone in zones:
                update_state(state, self._zone_change("10", zone))
            return state

        def enemy_entry():
            state = LogState()
            state.my_player_id = "1"
            state.add_entity("20", self._card("TEST_002", "2", "DECK"))
            update_state(state, self._zone_change("20", "HAND"))
            return state

        late_controller = LogState()
        late_controller.my_player_id = "1"
        late_controller.add_entity("30", self._card("TEST_003", "0", "HAND"))
        update_state(late_controller, LineInfoContainer(
            LOG_LINE_TAG_CHANGE, entity="30", tag="CONTROLLER", value="1"))

        full_entity = LogState()
        full_entity.my_player_id = "1"
        update_state(full_entity, LineInfoContainer(
            LOG_LINE_FULL_ENTITY, entity="40", card="TEST_004"))
        update_state(full_entity, LineInfoContainer(
            LOG_LINE_TAG, tag="ZONE", value="HAND"))
        update_state(full_entity, LineInfoContainer(
            LOG_LINE_TAG, tag="CONTROLLER", value="1"))

        cases = (
            ("every friendly transition",
             transitions("HAND", "PLAY", "HAND", "HAND"), 2, "10", None),
            ("enemy hand entry", enemy_entry(), 0, "20", None),
            ("controller known after zone",
             late_controller, 1, "30", None),
            ("full entity tag sequence", full_entity, 1, "40", "HAND"),
            ("entry survives leaving hand",
             transitions("HAND", "PLAY"), 1, "10", "PLAY"),
        )
        for label, state, expected, entity_id, zone in cases:
            with self.subTest(case=label):
                self.assertEqual(expected, state.hand_entry_count)
                if zone is not None:
                    self.assertEqual(zone, state.entity_dict[entity_id].zone)

    def test_rewind_full_entity_rebuilds_hand_membership(self):
        state = LogState()
        state.my_player_id = "2"
        state.add_entity("121", self._card("TLC_245", "2", "HAND"))
        self.assertEqual(["121"], [e for e in state.entity_dict
                                   if state.entity_dict[e].zone == "HAND"])

        # 回溯把经战吼入手的手牌卡重定义为 SETASIDE 里的候选：整实体重建后
        # 旧 ZONE=HAND 被清掉，手牌不再多算这张幻影。
        update_state(state, LineInfoContainer(
            LOG_LINE_FULL_ENTITY, entity="121", card="TIME_046"))
        update_state(state, LineInfoContainer(
            LOG_LINE_TAG, tag="CONTROLLER", value="2"))
        update_state(state, LineInfoContainer(
            LOG_LINE_TAG, tag="ZONE", value="SETASIDE"))

        entity = state.entity_dict["121"]
        self.assertEqual("TIME_046", entity.card_id)
        self.assertEqual("SETASIDE", entity.zone)
        self.assertEqual([], [e for e in state.entity_dict
                              if state.entity_dict[e].zone == "HAND"])

        # 若重置后的 tag 块重新声明 ZONE=HAND（未被顶掉的正常手牌），
        # 实体应重新进入手牌模型 —— 手牌成员由日志权威决定。
        state.add_entity("54", self._card("JAIL_457", "2", "HAND"))
        update_state(state, LineInfoContainer(
            LOG_LINE_FULL_ENTITY, entity="54", card="JAIL_457"))
        update_state(state, LineInfoContainer(
            LOG_LINE_TAG, tag="CONTROLLER", value="2"))
        update_state(state, LineInfoContainer(
            LOG_LINE_TAG, tag="ZONE", value="HAND"))
        update_state(state, LineInfoContainer(
            LOG_LINE_TAG, tag="ZONE_POSITION", value="1"))

        self.assertEqual("HAND", state.entity_dict["54"].zone)
        self.assertEqual("JAIL_457", state.entity_dict["54"].card_id)

    @staticmethod
    def _gs(body):
        # 还原完整日志行前缀(带毫秒与 GameState 前缀), 让 parse_line 能解析。
        return (f"D 00:30:08.1870240 GameState.DebugPrintPower() - "
                f"    {body}")

    def test_parse_line_reads_updating_full_entities(self):
        cases = (
            ("FULL_ENTITY - Updating [entityName=远古迅猛龙 id=121 "
             "zone=HAND zonePos=6 cardId=TLC_245 player=2] CardID=TIME_046",
             LOG_LINE_FULL_ENTITY, "entity", "121"),
            ("FULL_ENTITY - Updating YOURNAME CardID=",
             LOG_LINE_FULL_ENTITY_PLAYER, "name", "YOURNAME"),
        )
        for body, line_type, field, value in cases:
            with self.subTest(body=body[:30]):
                container = parse_line(self._gs(body))

                self.assertEqual(line_type, container.line_type)
                self.assertEqual(value, container.info_dict[field])

    def test_player_updating_restores_mana_onto_player_entity(self):
        # GAME_RESET 里玩家实体用裸名 FULL_ENTITY - Updating 重新 dump：
        # current_update_id 必须指到正确的玩家实体，否则法力会写到别的实体上。
        state = LogState()
        state.my_player_id = "2"
        state.oppo_player_id = "1"
        my_entity = PlayerEntity()
        my_entity.set_tag("CARDTYPE", "PLAYER")
        my_entity.set_tag("CONTROLLER", "2")
        oppo_entity = PlayerEntity()
        oppo_entity.set_tag("CARDTYPE", "PLAYER")
        oppo_entity.set_tag("CONTROLLER", "1")
        state.add_player_entity("3", "2", my_entity)
        state.add_player_entity("2", "1", oppo_entity)
        state.my_name = "YOURNAME#1234"

        update_state(state, LineInfoContainer(
            LOG_LINE_FULL_ENTITY_PLAYER, name="YOURNAME"))
        update_state(state, LineInfoContainer(
            LOG_LINE_TAG, tag="RESOURCES", value="5"))
        update_state(state, LineInfoContainer(
            LOG_LINE_TAG, tag="RESOURCES_USED", value="2"))

        self.assertEqual("5", state.entity_dict["3"].query_tag("RESOURCES"))
        self.assertEqual("2", state.entity_dict["3"].query_tag("RESOURCES_USED"))
        self.assertEqual("0", state.entity_dict["2"].query_tag("RESOURCES"))


if __name__ == "__main__":
    unittest.main()
