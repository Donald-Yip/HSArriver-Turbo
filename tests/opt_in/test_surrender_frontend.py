"""Opt-in frontend round-trip check: run web/index.html's script in a fake DOM.

The test intentionally lives under ``tests/opt_in``. That directory has no
``__init__.py``, so ``python -m unittest discover -s tests -t .`` does not recurse
into it. Run it explicitly when Node is available:

    uv run python -m unittest tests.opt_in.test_surrender_frontend -v

Set ``HSL_NODE`` when Node is not on PATH. Otherwise the test auto-discovers
``node`` via PATH or a standard Node.js installation under an environment-derived
Program Files/LOCALAPPDATA directory.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from tests.helpers.node_runtime import discover_node

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tests" / "frontend_surrender_roundtrip.js"


class FrontendRoundTripTests(unittest.TestCase):
    def test_nested_config_survives_the_form(self):
        node = discover_node()
        if node is None:
            self.skipTest("Node executable not found; set HSL_NODE to opt in")
        proc = subprocess.run(
            [node, str(SCRIPT)],
            cwd=str(ROOT),
            capture_output=True,
            encoding="utf-8",
            errors="strict",
            timeout=60,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(proc.returncode, 0, output)
        self.assertIn("OK: A 且 (B 或 C)", output)
        self.assertIn("OK: 非 (B 或 C)", output)
        self.assertIn("OK: 收起状态下保存不丢条件", output)


if __name__ == "__main__":
    unittest.main()
