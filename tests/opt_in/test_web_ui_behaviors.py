"""Opt-in behavior tests for the real web/index.html script in a fake DOM."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from tests.helpers.node_runtime import discover_node


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tests" / "web_ui_behaviors.js"


class WebUiBehaviorTests(unittest.TestCase):
    def _run_group(self, group: str) -> None:
        node = discover_node()
        if node is None:
            self.skipTest("Node executable not found; set HSL_NODE to opt in")
        proc = subprocess.run(
            [node, str(SCRIPT), group],
            cwd=str(ROOT),
            capture_output=True,
            encoding="utf-8",
            errors="strict",
            timeout=60,
            check=False,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(0, proc.returncode, output)
        self.assertIn(f"OK: {group}", output)

    def test_selfcheck_collapse_behavior(self) -> None:
        self._run_group("collapse")

    def test_settings_controls_round_trip(self) -> None:
        self._run_group("settings")

    def test_overlay_and_version_render_behavior(self) -> None:
        self._run_group("overlay")
