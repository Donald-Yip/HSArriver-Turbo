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

    def test_web_ui_stdio_reconfiguration_is_utf8_and_replacement_safe(self) -> None:
        script = (
            "import io, sys\n"
            "import web_ui\n"
            "class Stream:\n"
            "    def __init__(self): self.calls=[]\n"
            "    def reconfigure(self, **kwargs): self.calls.append(kwargs)\n"
            "out, err = Stream(), Stream()\n"
            "original_stdout, original_stderr = sys.stdout, sys.stderr\n"
            "try:\n"
            "    sys.stdout, sys.stderr = out, err\n"
            "    web_ui._configure_stdio()\n"
            "finally:\n"
            "    sys.stdout, sys.stderr = original_stdout, original_stderr\n"
            "assert out.calls == [{'encoding':'utf-8','errors':'replace'}]\n"
            "assert err.calls == [{'encoding':'utf-8','errors':'replace'}]\n"
        )
        proc = subprocess.run(
            [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-c", script],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(0, proc.returncode, proc.stderr)

    def test_overlay_and_version_render_behavior(self) -> None:
        self._run_group("overlay")
