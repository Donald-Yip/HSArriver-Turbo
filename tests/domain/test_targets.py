from __future__ import annotations

import unittest

from hslegend.domain import TargetResolver
from tests.domain.support import (
    CHOICE_OPTION,
    FRIENDLY_BOARD,
    FRIENDLY_HAND,
    FRIENDLY_HERO,
    FRIENDLY_WEAPON,
    OPPONENT_BOARD,
    snapshot,
)


class TargetResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = snapshot()
        self.resolver = TargetResolver()

    def test_resolve_by_side(self) -> None:
        result = self.resolver.resolve(self.snapshot, side="friendly")
        self.assertIn(FRIENDLY_HAND, result)
        self.assertNotIn(OPPONENT_BOARD, result)
        self.assertEqual(result, self.resolver.resolve(self.snapshot, side="FRIENDLY"))

    def test_resolve_by_zone_and_zone_position(self) -> None:
        self.assertEqual(
            (FRIENDLY_HAND,),
            self.resolver.resolve(self.snapshot, side="friendly", zone="hand"),
        )
        self.assertEqual(
            (FRIENDLY_BOARD,),
            self.resolver.resolve(
                self.snapshot, side="friendly", zone="board", zone_pos=1
            ),
        )
        self.assertEqual((), self.resolver.resolve(self.snapshot, zone="board", zone_pos=9))

    def test_resolve_by_entity_id(self) -> None:
        self.assertEqual(
            (FRIENDLY_WEAPON,),
            self.resolver.resolve(self.snapshot, entity_id=FRIENDLY_WEAPON.entity_id),
        )
        self.assertEqual((), self.resolver.resolve(self.snapshot, entity_id=999))

    def test_resolve_choice_entities(self) -> None:
        self.assertEqual(
            (CHOICE_OPTION,),
            self.resolver.resolve(self.snapshot, zone="choice"),
        )

    def test_contains_requires_exact_snapshot_identity(self) -> None:
        self.assertTrue(self.resolver.contains(self.snapshot, FRIENDLY_HAND))
        stale_zone = self.snapshot.entity(FRIENDLY_HAND.entity_id)
        assert stale_zone is not None
        changed = type(stale_zone)(
            stale_zone.entity_id,
            stale_zone.card_id,
            stale_zone.side,
            "board",
            stale_zone.zone_pos,
        )
        self.assertFalse(self.resolver.contains(self.snapshot, changed))
        self.assertTrue(self.resolver.contains(self.snapshot, changed, exact=False))
        self.assertFalse(self.resolver.contains(self.snapshot, FRIENDLY_HERO.__class__(999, "x", "friendly", "hero", 0)))

    def test_find_one_is_deterministic(self) -> None:
        self.assertEqual(
            FRIENDLY_BOARD,
            self.resolver.find_one(self.snapshot, side="friendly", zone="board"),
        )
        self.assertIsNone(self.resolver.find_one(self.snapshot, entity_id=999))


if __name__ == "__main__":
    unittest.main()
