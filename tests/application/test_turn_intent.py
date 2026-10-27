from __future__ import annotations

import unittest
from dataclasses import replace

from hslegend.application.use_cases.turn_intent import TurnIntentError, TurnIntentParser
from hslegend.domain import EntityRef, GameSnapshot, PlayerView


def turn_snapshot(
    *,
    hand_positions: tuple[int, ...] = (9, 2, 5),
    board_positions: tuple[int, ...] = (7, 1, 4),
    opponent_positions: tuple[int, ...] = (8, 3),
    opponent_weapon: bool = False,
    is_my_turn: bool = True,
) -> GameSnapshot:
    hand = tuple(
        EntityRef(100 + index, f"hand-{index}", "friendly", "hand", pos)
        for index, pos in enumerate(hand_positions)
    )
    board = tuple(
        EntityRef(200 + index, f"board-{index}", "friendly", "board", pos)
        for index, pos in enumerate(board_positions)
    )
    opponent_board = tuple(
        EntityRef(300 + index, f"enemy-{index}", "opponent", "board", pos)
        for index, pos in enumerate(opponent_positions)
    )
    return GameSnapshot(
        schema_version=1,
        snapshot_id="snap-turn",
        log_revision=11,
        game_generation=4,
        observed_at=100.0,
        turn_number=6,
        is_my_turn=is_my_turn,
        is_end=False,
        my_player=PlayerView(
            player_id=1,
            side="friendly",
            hero=EntityRef(1, "hero-friendly", "friendly", "hero", 0),
            hand=hand,
            board=board,
        ),
        opponent=PlayerView(
            player_id=2,
            side="opponent",
            hero=EntityRef(2, "hero-opponent", "opponent", "hero", 0),
            board=opponent_board,
            weapon=(
                EntityRef(400, "enemy-weapon", "opponent", "weapon", 0)
                if opponent_weapon else None
            ),
        ),
    )


class TurnIntentParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TurnIntentParser()
        self.snapshot = turn_snapshot()

    def test_parses_end_turn_and_hero_power(self) -> None:
        end = self.parser.parse(self.snapshot, "结束回合")
        self.assertEqual("end_turn", end.action_type)
        self.assertIsNone(end.source)

        power = self.parser.parse(self.snapshot, "使用英雄技能")
        self.assertEqual("hero_power", power.action_type)
        self.assertEqual(self.snapshot.my_player.hero, power.source)

    def test_parses_every_play_card_type_from_deterministic_hand_slots(self) -> None:
        for card_type in ("随从", "法术", "武器", "地标", "英雄"):
            with self.subTest(card_type=card_type):
                intent = self.parser.parse(self.snapshot, f"打出2号位{card_type}")
                self.assertEqual("play_card", intent.action_type)
                self.assertEqual(sorted(self.snapshot.my_player.hand, key=lambda entity: entity.zone_pos)[1], intent.source)
                self.assertEqual("hand-2", intent.card_id)
                self.assertEqual(1, intent.choice_index)

    def test_parses_minion_attack_with_enemy_board_target(self) -> None:
        intent = self.parser.parse(
            self.snapshot,
            "操作2号位随从攻击\n目标是敌方1号位随从",
        )
        self.assertEqual("attack", intent.action_type)
        self.assertEqual(sorted(self.snapshot.my_player.board, key=lambda entity: entity.zone_pos)[1], intent.source)
        self.assertEqual(sorted(self.snapshot.opponent.board, key=lambda entity: entity.zone_pos)[0], intent.target)
        self.assertEqual(1, intent.choice_index)

    def test_parses_hero_attacks_and_hero_targets(self) -> None:
        intent = self.parser.parse(
            self.snapshot,
            "操作我方英雄攻击\n目标是对方英雄",
        )
        self.assertEqual(self.snapshot.my_player.hero, intent.source)
        self.assertEqual(self.snapshot.opponent.hero, intent.target)
        self.assertIsNone(intent.choice_index)

        friendly = self.parser.parse(
            self.snapshot,
            "使用英雄技能\n目标是己方英雄",
        )
        self.assertEqual(self.snapshot.my_player.hero, friendly.target)

    def test_parses_friendly_and_enemy_slot_aliases(self) -> None:
        expected_targets = {
            "目标是我方2号位随从": sorted(self.snapshot.my_player.board, key=lambda entity: entity.zone_pos)[1],
            "目标是己方2号位": sorted(self.snapshot.my_player.board, key=lambda entity: entity.zone_pos)[1],
            "目标是对方2号位随从": sorted(self.snapshot.opponent.board, key=lambda entity: entity.zone_pos)[1],
            "目标是敌方2号位": sorted(self.snapshot.opponent.board, key=lambda entity: entity.zone_pos)[1],
        }
        for line, expected in expected_targets.items():
            with self.subTest(line=line):
                intent = self.parser.parse(self.snapshot, f"使用英雄技能\n{line}")
                self.assertEqual(expected, intent.target)

    def test_enforces_declared_weapon_and_hero_target_kinds(self) -> None:
        weapon_snapshot = turn_snapshot(opponent_weapon=True)
        weapon = self.parser.parse(
            weapon_snapshot,
            "使用英雄技能\n目标是敌方1号位武器",
        )
        self.assertEqual("enemy-weapon", weapon.target.card_id)
        self.assertEqual("weapon", weapon.target.zone)

        hero = self.parser.parse(
            self.snapshot,
            "使用英雄技能\n目标是我方1号位英雄",
        )
        self.assertEqual(self.snapshot.my_player.hero, hero.target)

        with self.assertRaises(TurnIntentError) as raised:
            self.parser.parse(
                self.snapshot,
                "使用英雄技能\n目标是敌方1号位武器",
            )
        self.assertEqual("target_weapon_missing", raised.exception.reason_code)

        with self.assertRaises(TurnIntentError) as raised:
            self.parser.parse(
                weapon_snapshot,
                "使用英雄技能\n目标是敌方2号位武器",
            )
        self.assertEqual("target_slot_invalid", raised.exception.reason_code)

    def test_rejects_malformed_entity_ownership(self) -> None:
        malformed = replace(
            self.snapshot,
            my_player=replace(
                self.snapshot.my_player,
                board=(
                    EntityRef(999, "wrong-side", "opponent", "hand", 1),
                ),
            ),
        )

        with self.assertRaises(TurnIntentError) as raised:
            self.parser.parse(malformed, "操作1号位随从攻击\n目标是敌方1号位随从")

        self.assertEqual("invalid_slot_ownership", raised.exception.reason_code)

    def test_parses_location_and_zero_based_destination(self) -> None:
        location = self.parser.parse(self.snapshot, "操作2号位地标")
        self.assertEqual("use_location", location.action_type)
        self.assertEqual(sorted(self.snapshot.my_player.board, key=lambda entity: entity.zone_pos)[1], location.source)

        play = self.parser.parse(
            self.snapshot,
            "打出1号位随从\n放置于我方3号位",
        )
        self.assertEqual(2, play.destination)

    def test_rejects_unknown_and_ambiguous_text(self) -> None:
        cases = {
            "随便打一张": "unsupported_instruction",
            "结束回合\n使用英雄技能": "multiple_actions",
            "使用英雄技能\n目标是我方1号位\n目标是敌方1号位": "duplicate_target",
            "使用英雄技能\n放置于我方1号位\n放置于我方2号位": "duplicate_destination",
        }
        for instruction, reason in cases.items():
            with self.subTest(instruction=instruction):
                with self.assertRaises(TurnIntentError) as raised:
                    self.parser.parse(self.snapshot, instruction)
                self.assertEqual(reason, raised.exception.reason_code)

    def test_duplicate_zone_positions_fail_closed(self) -> None:
        snapshot = turn_snapshot(board_positions=(1, 1))
        with self.assertRaises(TurnIntentError) as raised:
            self.parser.parse(snapshot, "操作1号位随从攻击\n目标是敌方英雄")
        self.assertEqual("ambiguous_slot", raised.exception.reason_code)
    def test_rejects_missing_and_out_of_range_targets(self) -> None:
        cases = {
            "操作1号位随从攻击": "target_required",
            "操作9号位随从攻击\n目标是敌方英雄": "source_slot_invalid",
            "操作1号位随从攻击\n目标是敌方9号位随从": "target_slot_invalid",
            "打出1号位随从\n放置于我方9号位": "destination_slot_invalid",
        }
        for instruction, reason in cases.items():
            with self.subTest(instruction=instruction):
                with self.assertRaises(TurnIntentError) as raised:
                    self.parser.parse(self.snapshot, instruction)
                self.assertEqual(reason, raised.exception.reason_code)


if __name__ == "__main__":
    unittest.main()
