from __future__ import annotations

import unittest

from hslegend.domain import ActionIntent


class ActionIntentDestinationTests(unittest.TestCase):
    def test_destination_accepts_a_zero_based_non_negative_int(self) -> None:
        intent = ActionIntent("play_card", destination=2)
        self.assertEqual(2, intent.destination)

    def test_destination_rejects_invalid_values(self) -> None:
        for value in (-1, 1.0, True, "1"):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    ActionIntent("play_card", destination=value)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
