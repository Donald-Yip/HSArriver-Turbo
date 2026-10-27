# -*- coding: utf-8 -*-
"""CI gate contracts that must remain in the default headless suite."""

from __future__ import annotations

import io
import unittest

from tools.run_headless_tests import RequiredOptInResult


class RequiredOptInResultTests(unittest.TestCase):
    def test_required_skip_is_a_failure(self):
        class SkippingRequiredTest(unittest.TestCase):
            def test_skip(self):
                self.skipTest("forced missing prerequisite")

        suite = unittest.TestSuite([SkippingRequiredTest("test_skip")])
        stream = io.StringIO()
        result = unittest.TextTestRunner(
            stream=stream, resultclass=RequiredOptInResult).run(suite)

        self.assertEqual(1, len(result.failures))
        self.assertEqual([], result.skipped)
        self.assertFalse(result.wasSuccessful())
        self.assertIn(
            "required opt-in test skipped: forced missing prerequisite",
            result.failures[0][1],
        )


if __name__ == "__main__":
    unittest.main()
