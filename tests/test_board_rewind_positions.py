from types import SimpleNamespace
import unittest

from src.game_state.recommendation_adapter import adapt_action, ordered_board
from src.parser.recommendation_parser import RecommendationParser
from manual_controller import ManualController, UseLocationAction


class BoardRewindPositionTests(unittest.TestCase):
    def test_same_position_keeps_newer_rewind_branch_entity(self):
        state = SimpleNamespace(
            my_minions=[
                SimpleNamespace(entity_id="305", zone_pos=4, name="旧时间线"),
                SimpleNamespace(entity_id="306", zone_pos=4, name="当前时间线"),
                SimpleNamespace(entity_id="307", zone_pos=2, name="当前"),
            ],
            my_locations=[SimpleNamespace(entity_id="26", zone_pos=1, name="地标")],
            oppo_minions=[], oppo_locations=[],
        )

        board = ordered_board(state, "friendly")

        self.assertEqual(["26", "307", "306"],
                         [entry.entity.entity_id for entry in board])
        self.assertEqual([1, 2, 4], [entry.entity.zone_pos for entry in board])
        self.assertEqual(3, ManualController._board_slot_count(state, "friendly"))

    def test_location_action_survives_a_duplicate_minion_position(self):
        minions = [
            SimpleNamespace(entity_id="218", zone_pos=3, card_id="TIME_038t1",
                            cardtype="MINION", name="钟表先生"),
            SimpleNamespace(entity_id="305", zone_pos=4, card_id="TIME_042",
                            cardtype="MINION", name="旧时间线"),
            SimpleNamespace(entity_id="306", zone_pos=4, card_id="JAIL_448",
                            cardtype="MINION", name="当前时间线"),
            SimpleNamespace(entity_id="57", zone_pos=5, card_id="JAIL_513",
                            cardtype="MINION", name="笼中颅骨"),
        ]
        state = SimpleNamespace(
            game_num_turns_in_play=24, is_my_turn=True,
            my_minions=minions,
            my_locations=[SimpleNamespace(
                entity_id="26", zone_pos=1, card_id="JAIL_511",
                cardtype="LOCATION", name="孤守晶塔")],
            oppo_minions=[], oppo_locations=[],
        )
        proposed = RecommendationParser().parse(
            SimpleNamespace(frame_id="frame",
                            normalized_text="打法参考A\n操作1号位地标",
                            confidence=.99), 24, 7)

        adapted = adapt_action(proposed, state)

        self.assertIsInstance(adapted.manual_action, UseLocationAction)
        self.assertEqual("JAIL_511", adapted.manual_action.card_id)


if __name__ == "__main__":
    unittest.main()
