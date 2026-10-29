from contextlib import nullcontext
from types import SimpleNamespace
import unittest

from manual_controller import ClickExecutor, ManualController
from src.game_state.recommendation_adapter import (
    RecommendationStateError,
    adapt_action,
)
from src.parser.recommendation_parser import RecommendationParser


PANEL = (
    "打法参考A\n"
    "使用英雄技能\n"
    "吸血鬼之吻\n"
    "目标是己方6号位\n"
    "重生的翼手龙"
)


class RecordingClicks:
    def __init__(self):
        self.events = []

    def click_skill(self, slot="primary"):
        self.events.append(("skill", slot))

    def use_skill_no_point(self, slot="primary"):
        self.events.append(("skill_no_point", slot))

    def choose_my_minion(self, index, count):
        self.events.append(("friendly", index, count))

    def cancel_click(self):
        self.events.append("cancel")


def parse():
    return RecommendationParser().parse(
        SimpleNamespace(frame_id="frame", normalized_text=PANEL, confidence=.99),
        turn_number=3,
        log_revision=7,
    )


def state():
    minions = [
        SimpleNamespace(entity_id=str(index), zone_pos=index,
                        card_id="TEST_MINION", cardtype="MINION",
                        name=f"随从{index}")
        for index in range(1, 7)
    ]
    return SimpleNamespace(
        game_num_turns_in_play=3,
        is_my_turn=True,
        my_hand_cards=[],
        my_minions=minions,
        my_locations=[],
        oppo_minions=[],
        oppo_locations=[],
        my_hero=SimpleNamespace(entity_id="hero"),
        oppo_hero=SimpleNamespace(entity_id="oppo-hero"),
        my_hero_power=SimpleNamespace(entity_id="hp-secondary",
                                      card_id="JAIL_446hp"),
        my_hero_powers=[
            SimpleNamespace(entity_id="hp-primary", card_id="HERO_01bp"),
            SimpleNamespace(entity_id="hp-secondary", card_id="JAIL_446hp"),
        ],
    )


class SecondaryHeroPowerTests(unittest.TestCase):
    def test_parser_keeps_the_named_hero_power(self):
        proposed = parse()

        self.assertEqual("吸血鬼之吻", proposed.source_name)
        self.assertEqual(6, proposed.target.index)

    def test_adapter_uses_fixed_secondary_slot_and_binds_sixth_minion(self):
        action = adapt_action(parse(), state()).manual_action

        self.assertEqual("JAIL_446hp", action.card_id)
        self.assertEqual("hp-secondary", action.hero_power_entity_id)
        self.assertEqual("secondary", action.hero_power_slot)
        self.assertEqual(5, action.target.index)
        self.assertEqual("6", action.target.entity_id)

    def test_click_sequence_is_secondary_skill_then_sixth_minion(self):
        clicks = RecordingClicks()
        controller = ManualController(
            output_func=lambda _message: None,
            executor=ClickExecutor(
                click_module=clicks,
                sleep_func=lambda _seconds: None,
                action_context=nullcontext,
            ),
        )

        result = controller.execute(adapt_action(parse(), state()).manual_action,
                                    state())

        self.assertTrue(result.executed, result.message)
        self.assertEqual([
            ("skill", "secondary"),
            ("friendly", 5, 6),
            "cancel",
        ], clicks.events)

    def test_unknown_or_duplicate_power_name_is_rejected(self):
        proposed = parse()
        unknown = state()
        unknown.my_hero_powers = [
            SimpleNamespace(entity_id="hp-primary", card_id="HERO_01bp")]
        unknown.my_hero_power = unknown.my_hero_powers[0]
        with self.assertRaisesRegex(RecommendationStateError,
                                    "hero_power_name_unknown"):
            adapt_action(proposed, unknown)

        duplicate = state()
        duplicate.my_hero_powers.append(
            SimpleNamespace(entity_id="hp-copy", card_id="JAIL_446hp"))
        with self.assertRaisesRegex(RecommendationStateError,
                                    "hero_power_name_not_unique"):
            adapt_action(proposed, duplicate)


if __name__ == "__main__":
    unittest.main()
