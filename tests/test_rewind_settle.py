from contextlib import nullcontext
from types import SimpleNamespace
import threading
import unittest

from manual_controller import ClickExecutor, ManualController
from src.game_state.recommendation_adapter import adapt_action
from src.parser.recommendation_parser import RecommendationParser
from src.hslegend.adapters.legacy.card_queries import is_rewind_card


TEXT = "打法参考A\n打出1号位随从\n钟表先生克劳沃斯\n放置于我方2号位"


def proposed():
    return RecommendationParser().parse(
        SimpleNamespace(frame_id="frame", normalized_text=TEXT, confidence=.99),
        turn_number=3, log_revision=7)


def state(card_id="TIME_038"):
    card = SimpleNamespace(entity_id="hand-1", card_id=card_id,
                           cardtype="MINION", name="钟表先生克劳沃斯",
                           zone_pos=1)
    return SimpleNamespace(
        game_num_turns_in_play=3, is_my_turn=True,
        my_hand_cards=[card],
        my_minions=[SimpleNamespace(entity_id="board-1", card_id="CS2_182",
                                    cardtype="MINION", zone_pos=1, name="随从")],
        my_locations=[], oppo_minions=[], oppo_locations=[],
        my_board_slot_num=1, oppo_board_slot_num=0)


class RecordingClicks:
    def __init__(self):
        self.events = []

    def cancel_click(self): self.events.append("cancel")
    def choose_card(self, index, count): self.events.append(("hand", index, count))
    def put_minion(self, index, count): self.events.append(("place", index, count))


class RewindSettleTests(unittest.TestCase):
    def test_rewind_capability_comes_from_card_metadata(self):
        self.assertTrue(is_rewind_card("TIME_038"))
        self.assertTrue(is_rewind_card("TIME_038t1"))
        self.assertFalse(is_rewind_card("CATA_458"))

    def test_rewind_card_waits_three_seconds_after_placement(self):
        adapted = adapt_action(proposed(), state())
        self.assertEqual(3.0, adapted.manual_action.settle_delay_seconds)
        clicks = RecordingClicks()
        sleeps = []
        controller = ManualController(
            output_func=lambda _message: None,
            executor=ClickExecutor(click_module=clicks,
                                   action_context=nullcontext,
                                   sleep_func=sleeps.append),
        )

        result = controller.execute(adapted.manual_action, state())

        self.assertTrue(result.executed, result.message)
        self.assertEqual([("hand", 0, 1), ("place", 1, 1), "cancel"], clicks.events)
        self.assertEqual([3.0], sleeps)

    def test_stop_interrupts_rewind_settle_without_cleanup_click(self):
        adapted = adapt_action(proposed(), state())
        clicks = RecordingClicks()
        stopped = threading.Event()

        def put_minion(index, count):
            clicks.events.append(("place", index, count))
            stopped.set()

        clicks.put_minion = put_minion
        controller = ManualController(
            output_func=lambda _message: None,
            executor=ClickExecutor(click_module=clicks,
                                   action_context=nullcontext,
                                   stop_event=stopped),
        )

        result = controller.execute(adapted.manual_action, state())

        self.assertFalse(result.executed)
        self.assertTrue(result.cancelled)
        self.assertEqual([("hand", 0, 1), ("place", 1, 1)], clicks.events)


if __name__ == "__main__":
    unittest.main()
