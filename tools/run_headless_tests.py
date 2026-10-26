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


def opt_in_manifest() -> dict[str, object]:
    return {
        "mode": "opt-in modules are not collected by the default runner",
        "excluded_modules": [],
        "excluded_tests": [],
        "opt_in_modules": list(OPT_IN_MODULES),
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
    args = parser.parse_args(argv)

    root = args.root.resolve()
    sys.path.insert(0, str(root))
    suite, _modules = build_headless_suite(root)
    manifest = opt_in_manifest()
    if args.list_opt_in:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    if args.json_opt_in:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))

    count = suite.countTestCases()
    if count == 0:
        print("[headless-tests] ERROR: discovery selected zero tests", file=sys.stderr)
        return 2
    print(f"[headless-tests] selected {count} tests; opt-in modules not collected: {len(OPT_IN_MODULES)}")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
    print(
        f"[headless-tests] summary: run={result.testsRun} passed={passed} "
        f"failures={len(result.failures)} errors={len(result.errors)} skipped={len(result.skipped)}"
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
