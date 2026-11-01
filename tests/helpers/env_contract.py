"""Shared filesystem/config contracts for environment tests.

``requirements.txt`` 现在是**群友的 pip 安装清单**（不再是废弃文件）：
它必须包含 pyproject 的直接依赖（版本逐个一致），再加上 pyproject
``[tool.uv].constraint-dependencies`` 里那几条「必须同版本」的约束
（OpenCV 三件套）。两者合起来才是 uv 环境里实际装到的那套版本。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
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


def constraint_pins() -> dict[str, str]:
    """``[tool.uv].constraint-dependencies``（OpenCV 三件套必须同版本）。"""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    constraints = (data.get("tool") or {}).get("uv", {}).get(
        "constraint-dependencies", [])
    return parse_exact_pins(constraints)


def expected_requirements_pins() -> dict[str, str]:
    """requirements.txt 应有的全部 pin：直接依赖 + uv 约束依赖。"""
    return {**pyproject_pins(), **constraint_pins()}


def requirements_pins() -> dict[str, str]:
    return parse_exact_pins(REQUIREMENTS.read_text(encoding="utf-8").splitlines())
