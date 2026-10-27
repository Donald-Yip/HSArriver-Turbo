"""Read-only native preflight contract tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from types import SimpleNamespace

from hslegend.adapters.legacy.preflight import (
    PreflightCheck,
    PreflightProbes,
    PreflightReport,
    _default_ui_probe,
    run_preflight,
)


class PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.log_root = Path(self._temp.name)
        self.power_log_path = self.log_root / "Power.log"
        self.power_log_path.write_text("Power.log fixture", encoding="utf-8")

    def probes(self, **overrides):
        values = {
            "runtime": lambda: "C:/repo",
            "identity": lambda: ("Player#1234", str(self.log_root)),
            "power_log": lambda _root: str(self.power_log_path),
            "processes": lambda: ("Hearthstone.exe", "HSAng.exe"),
            "ui": lambda: {
                "foreground": True,
                "phase": "main_menu",
                "confidence": 0.95,
                "evidence_id": "frame-1",
            },
            "is_admin": lambda: True,
            "deck_slot": lambda index: (100 + index, 200),
        }
        values.update(overrides)
        return PreflightProbes(**values)

    def checks(self, report: PreflightReport):
        return {check.check_id: check for check in report.checks}

    def test_all_checks_pass_and_report_serializes(self) -> None:
        report = run_preflight(3, self.probes())

        self.assertTrue(report.ok)
        self.assertEqual("main_menu", report.ui_phase)
        self.assertIn("--allow-live", report.live_command)
        self.assertIn("--deck-index 3", report.live_command)
        payload = report.to_primitive()
        json.dumps(payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(3, payload["deck_index"])
        self.assertEqual(str(self.power_log_path), payload["checks"][3]["detail"])
        self.assertIn("frame-1", payload["checks"][7]["detail"])

    def test_runtime_failure_stops_with_typed_reason(self) -> None:
        def unavailable():
            raise RuntimeError("runtime unavailable")

        report = run_preflight(0, self.probes(runtime=unavailable))

        self.assertFalse(report.ok)
        self.assertEqual(1, len(report.checks))
        self.assertEqual("legacy_runtime", report.checks[0].check_id)
        self.assertEqual("legacy_runtime_unavailable", report.checks[0].reason_code)

    def test_placeholder_identity_and_missing_log_root_fail(self) -> None:
        missing = self.log_root / "missing"
        report = run_preflight(
            0,
            self.probes(identity=lambda: ("YOURNAME#1234", str(missing))),
        )
        checks = self.checks(report)

        self.assertFalse(report.ok)
        self.assertEqual("config_identity_missing", checks["config_identity"].reason_code)
        self.assertEqual("log_root_missing", checks["log_root"].reason_code)
        self.assertEqual("log_root_missing", checks["power_log"].reason_code)

    def test_non_existing_power_log_path_fails(self) -> None:
        missing = self.log_root / "missing" / "Power.log"
        report = run_preflight(0, self.probes(power_log=lambda _root: str(missing)))
        checks = self.checks(report)
        self.assertEqual("power_log_missing", checks["power_log"].reason_code)

    def test_missing_power_log_and_processes_fail(self) -> None:
        report = run_preflight(
            0,
            self.probes(
                power_log=lambda _root: None,
                processes=lambda: ("Other.exe",),
            ),
        )
        checks = self.checks(report)

        self.assertEqual("power_log_missing", checks["power_log"].reason_code)
        self.assertEqual(
            "hearthstone_not_running",
            checks["hearthstone_process"].reason_code,
        )
        self.assertEqual("hsang_not_running", checks["hsang_process"].reason_code)

    def test_foreground_and_unsafe_ui_fail(self) -> None:
        report = run_preflight(
            0,
            self.probes(ui=lambda: {"foreground": False, "phase": "in_game"}),
        )
        checks = self.checks(report)

        self.assertEqual(
            "hearthstone_not_foreground",
            checks["foreground"].reason_code,
        )
        self.assertEqual("unsafe_ui_state", checks["ui_state"].reason_code)
        self.assertEqual("in_game", report.ui_phase)

    def test_non_admin_and_invalid_deck_fail(self) -> None:
        def invalid_deck(_index):
            raise ValueError("outside deck grid")

        report = run_preflight(
            9,
            self.probes(
                is_admin=lambda: False,
                deck_slot=invalid_deck,
            ),
        )
        checks = self.checks(report)

        self.assertEqual("admin_required", checks["admin"].reason_code)
        self.assertEqual("deck_index_invalid", checks["deck_index"].reason_code)

    def test_malformed_probe_values_fail_closed(self) -> None:
        cases = (
            ("runtime", object(), "legacy_runtime_unavailable"),
            ("identity", ("only-one",), "config_probe_error"),
            ("power_log", object(), "power_log_probe_error"),
            ("processes", "Hearthstone.exe", "process_probe_error"),
            ("ui", {"foreground": "false", "phase": "main_menu"}, "ui_probe_error"),
            ("is_admin", 1, "admin_probe_error"),
            ("deck_slot", "not-a-point", "deck_index_invalid"),
        )
        for name, value, expected in cases:
            with self.subTest(name=name):
                override = {name: lambda *_args, value=value: value}
                report = run_preflight(0, self.probes(**override))
                reasons = {check.reason_code for check in report.checks if not check.ok}
                self.assertIn(expected, reasons)
                self.assertFalse(report.ok)

    def test_report_and_failed_check_reject_contradictions(self) -> None:
        with self.assertRaises(ValueError):
            PreflightCheck("admin", False, "", "missing reason")
        bad = PreflightCheck("admin", False, "admin_required", "elevate")
        with self.assertRaises(ValueError):
            PreflightReport(
                ok=True,
                deck_index=0,
                ui_phase="main_menu",
                checks=(bad,),
                live_command="uv run hslegend-onegame --allow-live --json",
            )
        with self.assertRaises(ValueError):
            PreflightReport(
                ok=True,
                deck_index=0,
                ui_phase="   ",
                checks=(PreflightCheck("legacy_runtime", True),),
                live_command="uv run hslegend-onegame --allow-live --json",
            )

    def test_default_ui_probe_never_starts_window_tracker(self) -> None:
        calls = []

        def capture_client_frame():
            raise AssertionError("window-tracker capture must not be used")

        runtime = SimpleNamespace(
            get_screen=SimpleNamespace(
                get_HS_hwnd=lambda: 10,
                is_hearthstone_input_ready=lambda hwnd: True,
                _bitblt_region=lambda box: calls.append(box) or "frame",
            ),
            display_mapping=SimpleNamespace(
                display_mapping=lambda hwnd: SimpleNamespace(client_box=(1, 2, 3, 4))
            ),
            battle_entry=SimpleNamespace(
                capture_client_frame=capture_client_frame,
                identify=lambda frame: {"state": "main_menu"},
            ),
        )

        result = _default_ui_probe(runtime)

        self.assertEqual(
            {
                "foreground": True,
                "phase": "main_menu",
                "confidence": 0.0,
                "evidence_id": "battle-entry:main_menu:0.000000",
            },
            result,
        )
        self.assertEqual([(1, 2, 3, 4)], calls)

    def test_probe_exceptions_are_typed(self) -> None:
        def fail():
            raise RuntimeError("probe exploded")

        report = run_preflight(
            0,
            self.probes(ui=fail, processes=fail),
        )
        checks = self.checks(report)

        self.assertEqual("process_probe_error", checks["process_probe"].reason_code)
        self.assertEqual("ui_probe_error", checks["foreground"].reason_code)
        self.assertEqual("ui_probe_error", checks["ui_state"].reason_code)

    def test_admin_probe_must_return_exact_bool(self) -> None:
        report = run_preflight(0, self.probes(is_admin=lambda: "yes"))
        self.assertEqual("admin_probe_error", self.checks(report)["admin"].reason_code)


if __name__ == "__main__":
    unittest.main()
