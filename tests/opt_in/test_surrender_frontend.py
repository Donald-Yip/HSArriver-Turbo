"""Opt-in frontend round-trip check: run web/index.html's script in a fake DOM.

The test intentionally lives under ``tests/opt_in``. That directory has no
``__init__.py``, so ``python -m unittest discover -s tests -t .`` does not recurse
into it. Run it explicitly when Node is available:

    uv run python -m unittest tests.native.test_surrender_frontend -v

Set ``HSL_NODE`` when Node is not on PATH. Otherwise the test auto-discovers
``node`` via PATH or a standard Node.js installation under an environment-derived
Program Files/LOCALAPPDATA directory.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tests" / "frontend_surrender_roundtrip.js"
NODE_ENV = "HSL_NODE"


def _configured_node() -> str | None:
    configured = os.environ.get(NODE_ENV, "").strip()
    if not configured:
        return None
    candidate = Path(configured).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    found = shutil.which(configured)
    if found:
        return found
    raise FileNotFoundError(f"{NODE_ENV} does not name a Node executable: {configured}")


def _discover_node() -> str | None:
    configured = _configured_node()
    if configured:
        return configured

    for command in ("node", "node.exe"):
        found = shutil.which(command)
        if found:
            return found

    candidates: list[Path] = []
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(variable)
        if base:
            candidates.append(Path(base) / "nodejs" / "node.exe")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "nodejs" / "node.exe")

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


class FrontendRoundTripTests(unittest.TestCase):
    def test_nested_config_survives_the_form(self):
        node = _discover_node()
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
