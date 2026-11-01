"""Environment-only checks run explicitly in CI tooling mode."""

from __future__ import annotations

import importlib
import importlib.metadata
import sys
import unittest

import selfcheck
from tests.helpers.env_contract import (
    OPENCV_DISTRIBUTIONS,
    REQUIRED_IMPORTS,
    REQUIREMENTS,
    pyproject_pins,
)


class InstalledEnvironmentTests(unittest.TestCase):
    def test_python_is_312(self) -> None:
        self.assertEqual((3, 12), sys.version_info[:2])

    def test_installed_direct_versions(self) -> None:
        expected = pyproject_pins()
        installed = {
            distribution: importlib.metadata.version(distribution)
            for distribution in expected
        }
        self.assertEqual(expected, installed)

    def test_requirements_documents_the_pip_install_path(self) -> None:
        """群友按 requirements.txt 装：文件里必须写清 3.12 + pip 安装命令。"""
        text = REQUIREMENTS.read_text(encoding="utf-8")
        self.assertIn("pip install -r requirements.txt", text)
        self.assertIn("3.12", text)
        self.assertIn("web_ui.py", text)

    def test_opencv_distributions_and_runtime_agree(self) -> None:
        expected = pyproject_pins()["opencv-python-headless"]
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


class InstalledSelfcheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = selfcheck.run_checks()

    def test_python_312_is_reported_ok(self) -> None:
        item = next(i for i in self.result["items"] if i["key"] == "python")
        self.assertEqual(selfcheck.STATUS_OK, item["status"])
        self.assertTrue(self.result["python"].startswith("3.12"))

    def test_all_declared_dependencies_are_importable(self) -> None:
        failing = [
            i["label"]
            for i in self.result["items"]
            if i["key"] in {
                "pywin32",
                "opencv-python-headless",
                "numpy",
                "pillow",
                "paddleocr",
                "paddlepaddle",
            }
            and i["status"] == selfcheck.STATUS_FAIL
        ]
        self.assertEqual([], failing)

    def test_environment_items_are_present(self) -> None:
        keys = {i["key"] for i in self.result["items"]}
        self.assertLessEqual({"admin", "client", "dpi", "log-root"}, keys)
        self.assertNotIn("resolution", keys)

    def test_running_interpreter_is_the_recommended_one(self) -> None:
        item = selfcheck.python_item()
        self.assertEqual(selfcheck.STATUS_OK, item["status"])
        self.assertEqual((3, 12), selfcheck.EXPECTED_PYTHON)
