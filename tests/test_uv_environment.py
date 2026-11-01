# -*- coding: utf-8 -*-
"""Static manifest parity contract; installed-environment checks are opt-in."""

from __future__ import annotations

import unittest

from tests.helpers.env_contract import (
    REQUIREMENTS,
    expected_requirements_pins,
    requirements_pins,
)


class UvManifestContractTests(unittest.TestCase):
    def test_pyproject_and_requirements_pins_match(self) -> None:
        """requirements.txt（群友 pip 安装清单）= 直接依赖 + uv 约束依赖。"""
        self.assertEqual(expected_requirements_pins(), requirements_pins())

    def test_requirements_is_a_plain_pip_install_list(self) -> None:
        """它会被 `pip install -r requirements.txt` 直接执行：
        只能是注释/空行/精确 pin，不能出现 -e、索引地址等 pip 选项。"""
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
            declaration = line.split("#", 1)[0].strip()
            if declaration:
                self.assertRegex(declaration, r"^[A-Za-z0-9_.-]+==[^\s;]+$")


if __name__ == "__main__":
    unittest.main()
