"""Opt-in Windows smoke tests for the real process-enumeration path.

These tests intentionally live under ``tests/opt_in``. The directory has no
``__init__.py``, so default discovery rooted at ``tests`` does not collect them.
Run explicitly on Windows:

    uv run python -m unittest tests.native.test_process_enumeration -v
"""

from __future__ import annotations

import os
import sys
import unittest

from src.safety.hearthstone_liveness import list_process_names


class NativeProcessTests(unittest.TestCase):
    """Exercise ctypes process enumeration without injected fakes."""

    def test_can_enumerate_processes(self):
        names = list_process_names()
        if names is None:
            self.skipTest("current environment does not support process enumeration")
        self.assertTrue(names)

    def test_finds_this_python_process(self):
        names = list_process_names()
        if names is None:
            self.skipTest("current environment does not support process enumeration")
        exe = os.path.basename(sys.executable).lower()
        self.assertIn(exe, names)


if __name__ == "__main__":
    unittest.main()
