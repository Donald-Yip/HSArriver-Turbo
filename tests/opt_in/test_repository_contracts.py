"""Explicit CI/tooling checks that depend on repository or machine state."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class RepositoryHygieneTests(unittest.TestCase):
    def test_template_is_not_ignored_by_git(self) -> None:
        """The template must stay tracked; only the machine-local env file is ignored."""
        proc = subprocess.run(
            ["git", "check-ignore", "--quiet", "env.example.json"],
            cwd=str(ROOT),
            capture_output=True,
            check=False,
        )

        self.assertEqual(
            1,
            proc.returncode,
            (
                "git check-ignore must report exactly 1 for a tracked template; "
                f"got {proc.returncode}; stderr={proc.stderr!r}"
            ),
        )
