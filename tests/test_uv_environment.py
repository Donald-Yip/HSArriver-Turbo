# -*- coding: utf-8 -*-
"""Smoke and parity checks for the uv-managed development environment."""

from __future__ import annotations

import importlib
import importlib.metadata
from pathlib import Path
import re
import sys
import tomllib
import unittest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
DEPRECATED_REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
REQUIRED_IMPORTS = ("cv2", "numpy", "PIL", "win32api", "paddleocr")
OPENCV_DISTRIBUTIONS = (
    "opencv-python",
    "opencv-python-headless",
    "opencv-contrib-python",
)


def _normalize_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_exact_pins(lines: list[str]) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in lines:
        declaration = line.split("#", 1)[0].strip()
        if not declaration:
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", declaration)
        if not match:
            raise ValueError(f"not an exact dependency pin: {declaration!r}")
        pins[_normalize_distribution(match.group(1))] = match.group(2)
    return pins


def _pyproject_pins() -> dict[str, str]:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    return _parse_exact_pins(project["dependencies"])


class UvEnvironmentSmokeTest(unittest.TestCase):
    def test_python_is_312(self) -> None:
        self.assertEqual((3, 12), sys.version_info[:2])

    def test_manifest_parity_and_installed_direct_versions(self) -> None:
        pyproject_pins = _pyproject_pins()
        requirements_pins = _parse_exact_pins(
            DEPRECATED_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        )
        self.assertEqual(pyproject_pins, requirements_pins)

        installed = {
            distribution: importlib.metadata.version(distribution)
            for distribution in pyproject_pins
        }
        self.assertEqual(pyproject_pins, installed)

    def test_deprecated_requirements_has_r02_cleanup_note(self) -> None:
        text = DEPRECATED_REQUIREMENTS.read_text(encoding="utf-8")
        self.assertIn("DEPRECATED", text)
        self.assertIn("Do not install with pip", text)
        self.assertIn("R02 cleanup", text)

    def test_opencv_distributions_and_runtime_agree(self) -> None:
        expected = _pyproject_pins()["opencv-python-headless"]
        installed = {
            distribution: importlib.metadata.version(distribution)
            for distribution in OPENCV_DISTRIBUTIONS
        }
        self.assertEqual(
            {distribution: expected for distribution in OPENCV_DISTRIBUTIONS},
            installed,
        )

        cv2 = importlib.import_module("cv2")
        self.assertEqual("4.10.0", cv2.__version__)
        for distribution, version in installed.items():
            with self.subTest(distribution=distribution):
                self.assertTrue(
                    version.startswith(cv2.__version__ + "."),
                    f"{distribution} {version} disagrees with cv2 {cv2.__version__}",
                )

    def test_required_runtime_modules_import(self) -> None:
        for module_name in REQUIRED_IMPORTS:
            with self.subTest(module=module_name):
                self.assertIsNotNone(importlib.import_module(module_name))


if __name__ == "__main__":
    unittest.main()
