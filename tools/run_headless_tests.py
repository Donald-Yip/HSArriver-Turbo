#!/usr/bin/env python3
"""Run the default headless unittest suite.

The default suite is assembled only from top-level ``tests/test_*.py`` modules.
Deterministic integration tests live under ``tests/opt_in`` and run explicitly
with ``--opt-in``. Tests requiring a real Hearthstone window live under
``tests/native_live`` and run only with ``--native-live``. Directories are
discovered without importing their modules until the selected gate starts.
"""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from pathlib import Path

def _package_modules(root: Path, package: str) -> list[str]:
    directory = root.joinpath(*package.split("."))
    if not directory.is_dir():
        return []
    return [
        f"{package}.{path.stem}"
        for path in sorted(directory.glob("test_*.py"))
        if path.is_file()
    ]


def opt_in_modules(root: Path) -> list[str]:
    return _package_modules(root, "tests.opt_in")


def native_live_modules(root: Path) -> list[str]:
    return _package_modules(root, "tests.native_live")


class RequiredOptInResult(unittest.TextTestResult):
    """Opt-in test result where a skip is a gate failure, not success."""

    def addSkip(self, test, reason):
        error = (AssertionError,
                 AssertionError(f"required opt-in test skipped: {reason}"),
                 None)
        super().addFailure(test, error)


def default_test_modules(root: Path) -> list[str]:
    """Return only the top-level default test modules.

    ``glob`` is intentionally non-recursive. Nested opt-in and native-live
    directories are never included by default.
    """
    return [f"tests.{path.stem}" for path in sorted((root / "tests").glob("test_*.py")) if path.is_file()]


def build_headless_suite(root: Path) -> tuple[unittest.TestSuite, list[str]]:
    loader = unittest.TestLoader()
    modules = default_test_modules(root)
    if not modules:
        raise RuntimeError("no top-level tests/test_*.py modules found")
    return loader.loadTestsFromNames(modules), modules


def build_opt_in_suite(root: Path) -> tuple[unittest.TestSuite, list[str]]:
    loader = unittest.TestLoader()
    modules = opt_in_modules(root)
    return loader.loadTestsFromNames(modules), modules


def build_native_live_suite(root: Path) -> tuple[unittest.TestSuite, list[str]]:
    loader = unittest.TestLoader()
    modules = native_live_modules(root)
    return loader.loadTestsFromNames(modules), modules


def opt_in_manifest(root: Path | None = None) -> dict[str, object]:
    root = (root or Path(__file__).resolve().parents[1]).resolve()
    return {
        "mode": "opt-in modules are not collected by the default runner",
        "excluded_modules": [],
        "excluded_tests": [],
        "opt_in_modules": opt_in_modules(root),
        "opt_in_skips": "failure when running with --opt-in",
        "native_live_modules": native_live_modules(root),
    }


def native_live_manifest(root: Path | None = None) -> dict[str, object]:
    root = (root or Path(__file__).resolve().parents[1]).resolve()
    return {
        "mode": "native-live modules require a real Hearthstone window",
        "native_live_modules": native_live_modules(root),
        "native_live_skips": "failure when running with --native-live",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--list-exclusions",
        "--list-opt-in",
        dest="list_opt_in",
        action="store_true",
        help="Print the default/opt-in discovery manifest as JSON and exit",
    )
    parser.add_argument(
        "--json-exclusions",
        "--json-opt-in",
        dest="json_opt_in",
        action="store_true",
        help="Print the default/opt-in discovery manifest as JSON before running",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--opt-in",
        action="store_true",
        help=(
            "Run explicit Node/native/tooling modules; any skipped required "
            "test fails the gate"
        ),
    )
    mode_group.add_argument(
        "--native-live",
        action="store_true",
        help=(
            "Run tests requiring a real Hearthstone window; any skipped test "
            "fails the gate"
        ),
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    sys.path.insert(0, str(root))
    manifest = {
        "opt_in": opt_in_manifest(root),
        "native_live": native_live_manifest(root),
    }
    if args.list_opt_in:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    if args.json_opt_in:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))

    if args.opt_in:
        suite, modules = build_opt_in_suite(root)
        mode = "opt-in"
    elif args.native_live:
        suite, modules = build_native_live_suite(root)
        mode = "native-live"
    else:
        suite, modules = build_headless_suite(root)
        mode = "headless"

    count = suite.countTestCases()
    if count == 0:
        print("[headless-tests] ERROR: discovery selected zero tests", file=sys.stderr)
        return 2
    if mode == "opt-in":
        print(
            f"[headless-tests] selected {count} opt-in tests from "
            f"{len(modules)} modules; required skips fail"
        )
    elif mode == "native-live":
        print(
            f"[headless-tests] selected {count} native-live tests from "
            f"{len(modules)} modules; required skips fail"
        )
    else:
        print(
            f"[headless-tests] selected {count} tests; opt-in modules not "
            f"collected: {len(opt_in_modules(root))}; native-live modules not "
            f"collected: {len(native_live_modules(root))}"
        )
    result_class = (
        RequiredOptInResult
        if mode in {"opt-in", "native-live"}
        else unittest.TextTestResult
    )
    result = unittest.TextTestRunner(
        verbosity=2, resultclass=result_class).run(suite)
    passed = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
    print(
        f"[headless-tests] {mode} summary: run={result.testsRun} passed={passed} "
        f"failures={len(result.failures)} errors={len(result.errors)} "
        f"skipped={len(result.skipped)}"
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
