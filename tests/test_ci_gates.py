# -*- coding: utf-8 -*-
"""CI gate contracts that must remain in the default headless suite."""

from __future__ import annotations

import io
from pathlib import Path
import unittest

from tools.run_headless_tests import (
    RequiredOptInResult,
    native_live_manifest,
    opt_in_manifest,
)


class RunnerManifestTests(unittest.TestCase):
    def test_opt_in_and_native_live_modes_stay_separate(self):
        root = Path(__file__).resolve().parent.parent
        opt_in = opt_in_manifest(root)["opt_in_modules"]
        native_live = native_live_manifest(root)["native_live_modules"]

        self.assertIn("tests.opt_in.test_repository_contracts", opt_in)
        self.assertTrue(all(module.startswith("tests.opt_in.") for module in opt_in))
        self.assertTrue(
            all(module.startswith("tests.native_live.") for module in native_live)
        )
        self.assertFalse(set(opt_in) & set(native_live))


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
