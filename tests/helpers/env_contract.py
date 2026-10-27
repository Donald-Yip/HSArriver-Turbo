"""Shared filesystem/config contracts for environment tests."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
DEPRECATED_REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
REQUIRED_IMPORTS = ("cv2", "numpy", "PIL", "win32api", "paddleocr")
OPENCV_DISTRIBUTIONS = (
    "opencv-python",
    "opencv-python-headless",
    "opencv-contrib-python",
)


def normalize_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_exact_pins(lines: list[str]) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in lines:
        declaration = line.split("#", 1)[0].strip()
        if not declaration:
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", declaration)
        if not match:
            raise ValueError(f"not an exact dependency pin: {declaration!r}")
        pins[normalize_distribution(match.group(1))] = match.group(2)
    return pins


def pyproject_pins() -> dict[str, str]:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    return parse_exact_pins(project["dependencies"])


def requirements_pins() -> dict[str, str]:
    return parse_exact_pins(
        DEPRECATED_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
    )
