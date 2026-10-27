#!/usr/bin/env python3
"""Run the default headless unittest suite.

Opt-in integration tests live under ``tests/native`` and are never imported by
this runner. The default suite is assembled from top-level ``tests/test_*.py``
modules, rather than recursively discovering every importable test package. This
keeps Node, real process inspection, OCR models, a screen, and a running game out
of the default gate without relying on run-time filtering or silent skips.
"""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from pathlib import Path

OPT_IN_MODULES = (
    "tests.native.test_process_enumeration",
    "tests.native.test_surrender_frontend",
)


class RequiredOptInResult(unittest.TextTestResult):
    """Opt-in test result where a skip is a gate failure, not success."""

    def addSkip(self, test, reason):
        error = (AssertionError,
                 AssertionError(f"required opt-in test skipped: {reason}"),
                 None)
        super().addFailure(test, error)


def default_test_modules(root: Path) -> list[str]:
    """Return only the top-level default test modules.

    ``glob`` is intentionally non-recursive. Files under ``tests/native`` remain
    opt-in even if that directory later gains an ``__init__.py``.
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
    modules = list(OPT_IN_MODULES)
    return loader.loadTestsFromNames(modules), modules


def opt_in_manifest() -> dict[str, object]:
    return {
        "mode": "opt-in modules are not collected by the default runner",
        "excluded_modules": [],
        "excluded_tests": [],
        "opt_in_modules": list(OPT_IN_MODULES),
        "opt_in_skips": "failure when running with --opt-in",
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
    parser.add_argument(
        "--opt-in",
        action="store_true",
        help=(
            "Run the explicit Node/native opt-in modules; any skipped required "
            "test fails the gate"
        ),
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    sys.path.insert(0, str(root))
    manifest = opt_in_manifest()
    if args.list_opt_in:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    if args.json_opt_in:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))

    if args.opt_in:
        suite, modules = build_opt_in_suite(root)
        mode = "opt-in"
    else:
        suite, modules = build_headless_suite(root)
        mode = "headless"

    count = suite.countTestCases()
    if count == 0:
        print("[headless-tests] ERROR: discovery selected zero tests", file=sys.stderr)
        return 2
    if args.opt_in:
        print(
            f"[headless-tests] selected {count} opt-in tests from "
            f"{len(modules)} modules; required skips fail"
        )
    else:
        print(
            f"[headless-tests] selected {count} tests; opt-in modules not "
            f"collected: {len(OPT_IN_MODULES)}"
        )
    result_class = RequiredOptInResult if args.opt_in else unittest.TextTestResult
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
