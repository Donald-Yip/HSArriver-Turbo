#!/usr/bin/env python3
"""Run the default unittest suite with explicit headless exclusions.

R02B runs before R02C rationalizes the legacy tests. Until those tests move,
exclude the Node frontend test and the two real-process enumeration methods so
CI never depends on Node, native process inspection, OCR models, a screen, or a
running game.
"""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from pathlib import Path
from typing import Iterable

EXCLUDED_MODULES = frozenset({"tests.test_surrender_frontend"})
EXCLUDED_TEST_IDS = frozenset(
    {
        "tests.test_hearthstone_liveness.NativeProcessTests.test_can_enumerate_processes",
        "tests.test_hearthstone_liveness.NativeProcessTests.test_finds_this_python_process",
    }
)


def iter_cases(suite: unittest.TestSuite) -> Iterable[unittest.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_cases(item)
        else:
            yield item


def exclusion_reason(test: unittest.TestCase) -> str | None:
    test_id = test.id()
    module = test.__class__.__module__
    if module in EXCLUDED_MODULES or any(test_id.startswith(name + ".") for name in EXCLUDED_MODULES):
        return "Node/frontend integration is opt-in"
    if test_id in EXCLUDED_TEST_IDS:
        return "native process inspection is opt-in"
    return None


def build_headless_suite(root: Path) -> tuple[unittest.TestSuite, list[dict[str, str]]]:
    loader = unittest.TestLoader()
    discovered = loader.discover(start_dir=str(root / "tests"), pattern="test_*.py", top_level_dir=str(root))
    selected = unittest.TestSuite()
    excluded: list[dict[str, str]] = []
    for test in iter_cases(discovered):
        reason = exclusion_reason(test)
        if reason:
            excluded.append({"id": test.id(), "reason": reason})
        else:
            selected.addTest(test)
    return selected, excluded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--list-exclusions", action="store_true", help="Print exclusions as JSON and exit")
    parser.add_argument("--json-exclusions", action="store_true", help="Print exclusions as JSON before running")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    sys.path.insert(0, str(root))
    suite, excluded = build_headless_suite(root)
    exclusions = {
        "modules": sorted(EXCLUDED_MODULES),
        "tests": sorted(EXCLUDED_TEST_IDS),
        "matched": excluded,
    }
    if args.list_exclusions:
        print(json.dumps(exclusions, ensure_ascii=False, indent=2))
        return 0
    if args.json_exclusions:
        print(json.dumps(exclusions, ensure_ascii=False, indent=2))

    count = suite.countTestCases()
    if count == 0:
        print("[headless-tests] ERROR: discovery selected zero tests", file=sys.stderr)
        return 2
    print(f"[headless-tests] selected {count} tests; excluded {len(excluded)}")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
    print(
        f"[headless-tests] summary: run={result.testsRun} passed={passed} "
        f"failures={len(result.failures)} errors={len(result.errors)} skipped={len(result.skipped)}"
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
