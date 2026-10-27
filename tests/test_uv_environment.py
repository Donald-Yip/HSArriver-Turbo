# -*- coding: utf-8 -*-
"""Static uv manifest parity contract; installed-environment checks are opt-in."""

from __future__ import annotations

import unittest

from tests.helpers.env_contract import pyproject_pins, requirements_pins


class UvManifestContractTests(unittest.TestCase):
    def test_pyproject_and_requirements_pins_match(self) -> None:
        self.assertEqual(pyproject_pins(), requirements_pins())


if __name__ == "__main__":
    unittest.main()
