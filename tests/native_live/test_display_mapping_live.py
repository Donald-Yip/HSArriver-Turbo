"""Native-live display mapping smoke; requires a running Hearthstone window."""

from __future__ import annotations

import unittest

import display_mapping as dm


class DisplayMappingLiveTests(unittest.TestCase):
    def test_live_window_lookup(self) -> None:
        dm.clear_mapping_cache()
        try:
            mapping = dm.display_mapping()
        finally:
            dm.clear_mapping_cache()
        if mapping is None:
            self.skipTest("本机没有运行中的炉石窗口")

        self.assertGreater(mapping.width, 0)
        self.assertGreater(mapping.height, 0)
        self.assertEqual(mapping.reference, dm.REFERENCE_CLIENT)
