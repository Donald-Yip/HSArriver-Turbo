from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from hslegend.adapters.legacy.preflight import PreflightCheck, PreflightReport
from hslegend.application import LobbyPhase, OneGameCommand, OneGameStatus
from hslegend.application.use_cases.enter_battle import LobbyAction
from hslegend.application.use_cases.finish_battle import FinishBattleStatus
from hslegend.application.use_cases.mulligan import MulliganStatus
from hslegend.application.use_cases.turn import PlayTurnStatus
from hslegend.bootstrap import build_fake_one_game
from hslegend.entrypoints.onegame import main as cli_main

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ("-m", "hslegend.entrypoints.onegame")


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, *ENTRYPOINT, *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class FakeOneGameEndToEndTests(unittest.TestCase):
    def test_real_r08_r11_path_stops_after_one_settlement(self) -> None:
        harness = build_fake_one_game()
        result = harness.use_case.execute(
            OneGameCommand(
                "e2e-one-game",
                deck_index=0,
                max_turns=20,
                timeout_seconds=900.0,
            )
        )

        self.assertIs(OneGameStatus.COMPLETED, result.status)
        self.assertEqual("completed", result.reason_code)
        self.assertEqual("e2e-one-game", result.command_id)
        self.assertEqual("e2e-one-game", result.session_id)
        self.assertEqual(1, result.turns_played)
        self.assertEqual(1, result.actions_played)
        self.assertIs(LobbyPhase.RESULT, result.last_lobby_phase)
        self.assertEqual(len(result.evidence_ids), len(set(result.evidence_ids)))

        self.assertEqual(
            (
                "enter_traditional",
                "select_deck",
                "start_match",
                "mulligan_confirm",
                "end_turn",
            ),
            harness.scenario.dispatch.action_types,
        )
        self.assertEqual(5, len(harness.scenario.dispatch.calls))
        self.assertIs(LobbyPhase.RESULT, harness.scenario.lobby.phase)

        self.assertIsNotNone(result.enter_battle)
        assert result.enter_battle is not None
        self.assertEqual(
            (
                LobbyAction.ENTER_TRADITIONAL,
                LobbyAction.SELECT_DECK,
                LobbyAction.START_MATCH,
            ),
            tuple(dispatch.action for dispatch in result.enter_battle.dispatches),
        )
        self.assertIsNotNone(result.mulligan)
        assert result.mulligan is not None
        self.assertIs(MulliganStatus.COMPLETED, result.mulligan.status)
        self.assertEqual((), result.mulligan.selected_indexes)
        self.assertIsNotNone(result.mulligan.execution_record)

        self.assertEqual(1, len(result.turn_results))
        turn = result.turn_results[0]
        self.assertIs(PlayTurnStatus.ENDED, turn.status)
        self.assertEqual("end_turn", turn.reason_code)
        self.assertEqual(1, turn.actions)
        self.assertEqual(1, len(turn.action_records))

        self.assertIsNotNone(result.finish_battle)
        assert result.finish_battle is not None
        self.assertIs(FinishBattleStatus.COMPLETED, result.finish_battle.status)
        self.assertIs(LobbyPhase.RESULT, result.finish_battle.phase)

        required_evidence = {
            "demo-mulligan-snapshot",
            "recommendation-mulligan",
            "dispatch-mulligan_confirm-4",
            "demo-turn-snapshot",
            "recommendation-turn",
            "dispatch-end_turn-5",
            "demo-result-snapshot",
            "lobby-result",
        }
        self.assertTrue(required_evidence.issubset(set(result.evidence_ids)))
        self.assertEqual(2, len(harness.scenario.journal.records))


def live_ready_report(
    deck_index: int = 0,
    live_command: str = "uv run hslegend-onegame --allow-live --json",
) -> PreflightReport:
    return PreflightReport(
        ok=True,
        deck_index=deck_index,
        ui_phase="main_menu",
        checks=(
            PreflightCheck(
                "window_activation",
                True,
                detail="hearthstone-focused",
            ),
            PreflightCheck("foreground", True, detail="main_menu"),
        ),
        live_command=live_command,
    )


class OneGameCliTests(unittest.TestCase):
    def test_help_is_available(self) -> None:
        completed = run_cli("--help")
        self.assertEqual(0, completed.returncode)
        self.assertIn("hslegend-onegame", completed.stdout)
        self.assertIn("--fake", completed.stdout)
        self.assertIn("--preflight", completed.stdout)
        self.assertIn("--allow-live", completed.stdout)
        self.assertEqual("", completed.stderr)

    def test_native_without_allow_live_is_usage_error_without_builder_call(self) -> None:
        calls: list[str] = []

        def native_builder():
            calls.append("native")
            return build_fake_one_game()

        with self.assertRaises(SystemExit) as raised:
            cli_main(["--json"], native_builder=native_builder)
        self.assertEqual(2, raised.exception.code)
        self.assertEqual([], calls)

    def test_fake_and_preflight_modes_conflict(self) -> None:
        completed = run_cli("--fake", "--preflight", "--json")
        self.assertEqual(2, completed.returncode)
        self.assertIn("not allowed with argument", completed.stderr)

    def test_default_selects_native_builder(self) -> None:
        calls: list[str] = []

        def native_builder():
            calls.append("native")
            return build_fake_one_game()

        def fake_builder():
            calls.append("fake")
            return build_fake_one_game()

        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(
                ["--allow-live", "--json"],
                native_builder=native_builder,
                fake_builder=fake_builder,
                live_preflight_builder=lambda deck, **_kwargs: live_ready_report(deck),
            )

        self.assertEqual(0, code)
        self.assertEqual(["native"], calls)
        self.assertEqual("completed", json.loads(output.getvalue())["status"])

    def test_preflight_uses_injected_read_only_builder(self) -> None:
        calls: list[int] = []
        report = PreflightReport(
            ok=True,
            deck_index=2,
            ui_phase="main_menu",
            checks=(PreflightCheck("legacy_runtime", True),),
            live_command="uv run hslegend-onegame --allow-live --deck-index 2 --json",
        )

        def preflight_builder(deck_index: int, **kwargs):
            calls.append(deck_index)
            return report

        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(
                ["--preflight", "--deck-index", "2", "--json"],
                preflight_builder=preflight_builder,
            )

        self.assertEqual(0, code)
        self.assertEqual([2], calls)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(2, payload["deck_index"])

    def test_preflight_not_ready_returns_one(self) -> None:
        report = PreflightReport(
            ok=False,
            deck_index=0,
            ui_phase="unknown",
            checks=(PreflightCheck("admin", False, "admin_required", "elevate"),),
            live_command="uv run hslegend-onegame --allow-live --deck-index 0 --json",
        )
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(
                ["--preflight", "--json"],
                preflight_builder=lambda _index, **_kwargs: report,
            )
        self.assertEqual(1, code)
        self.assertEqual("admin_required", json.loads(output.getvalue())["checks"][0]["reason_code"])

    def test_preflight_rejects_allow_live_conflict(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            cli_main(["--preflight", "--allow-live", "--json"])
        self.assertEqual(2, raised.exception.code)

    def test_preflight_preserves_non_default_live_command(self) -> None:
        captured: dict[str, object] = {}
        def preflight_builder(deck_index: int, *, live_command: str):
            captured["deck_index"] = deck_index
            captured["live_command"] = live_command
            return PreflightReport(
                ok=True,
                deck_index=deck_index,
                ui_phase="deck_select",
                checks=(PreflightCheck("legacy_runtime", True),),
                live_command=live_command,
            )

        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(
                [
                    "--preflight",
                    "--deck-index",
                    "1",
                    "--command-id",
                    "custom-command",
                    "--max-turns",
                    "3",
                    "--timeout-seconds",
                    "45",
                    "--json",
                ],
                preflight_builder=preflight_builder,
            )

        self.assertEqual(0, code)
        self.assertEqual(1, captured["deck_index"])
        command = str(captured["live_command"])
        self.assertIn("--command-id custom-command", command)
        self.assertIn("--max-turns 3", command)
        self.assertIn("--timeout-seconds 45.0", command)
        self.assertEqual(command, json.loads(output.getvalue())["live_command"])

    def test_malformed_preflight_report_fails_closed(self) -> None:
        malformed = SimpleNamespace(
            ok=True,
            checks=(SimpleNamespace(ok=True, check_id="legacy_runtime"),),
            deck_index=0,
            ui_phase="main_menu",
            to_primitive=lambda: {"ok": True},
        )
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(
                ["--preflight", "--json"],
                preflight_builder=lambda _index, **_kwargs: malformed,
            )
        self.assertEqual(1, code)
        payload = json.loads(output.getvalue())
        self.assertEqual("preflight_report_invalid", payload["reason_code"])
        self.assertFalse(payload["ok"])

    def test_preflight_ignores_malformed_to_primitive_payload(self) -> None:
        report = SimpleNamespace(
            ok=True,
            checks=(
                SimpleNamespace(
                    ok=True,
                    check_id="legacy_runtime",
                    reason_code="",
                    detail="source-checkout",
                ),
            ),
            deck_index=0,
            ui_phase="main_menu",
            live_command="uv run hslegend-onegame --allow-live --json",
            to_primitive=lambda: {},
        )
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(
                ["--preflight", "--json"],
                preflight_builder=lambda _index, **_kwargs: report,
            )
        self.assertEqual(0, code)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(1, len(payload["checks"]))
        self.assertEqual("legacy_runtime", payload["checks"][0]["check_id"])

    def test_preflight_builder_exception_is_json_failure(self) -> None:
        def failing_builder(_deck_index: int, **_kwargs):
            raise RuntimeError("probe setup failed")

        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(
                ["--preflight", "--json"],
                preflight_builder=failing_builder,
            )
        self.assertEqual(1, code)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual("preflight_error", payload["reason_code"])

    def test_native_builder_typed_reason_is_preserved(self) -> None:
        class NativeFailure(RuntimeError):
            reason_code = "legacy_runtime_unavailable"

        def native_builder():
            raise NativeFailure("missing legacy runtime")

        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(
                ["--allow-live", "--json"],
                native_builder=native_builder,
                live_preflight_builder=lambda deck, **_kwargs: live_ready_report(deck),
            )
        self.assertEqual(1, code)
        payload = json.loads(output.getvalue())
        self.assertEqual("legacy_runtime_unavailable", payload["reason_code"])

    def test_live_preflight_failure_prevents_native_execution(self) -> None:
        calls: list[str] = []
        failed = PreflightReport(
            ok=False,
            deck_index=0,
            ui_phase="unknown",
            checks=(PreflightCheck("window_activation", False, "window_activation_failed", "focus"),),
            live_command="uv run hslegend-onegame --allow-live --json",
        )

        def native_builder():
            calls.append("native")
            return build_fake_one_game()

        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(
                ["--allow-live", "--json"],
                native_builder=native_builder,
                live_preflight_builder=lambda _deck, **_kwargs: failed,
            )
        self.assertEqual(1, code)
        self.assertEqual([], calls)
        self.assertEqual(
            "window_activation_failed",
            json.loads(output.getvalue())["checks"][0]["reason_code"],
        )

    def test_fake_selects_fake_builder(self) -> None:
        calls: list[str] = []

        def native_builder():
            calls.append("native")
            return build_fake_one_game()

        def fake_builder():
            calls.append("fake")
            return build_fake_one_game()

        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(
                ["--fake", "--json"],
                native_builder=native_builder,
                fake_builder=fake_builder,
            )

        self.assertEqual(0, code)
        self.assertEqual(["fake"], calls)
        self.assertEqual("completed", json.loads(output.getvalue())["status"])

    def test_empty_command_id_is_usage_error(self) -> None:
        completed = run_cli("--fake", "--json", "--command-id", "")
        self.assertEqual(2, completed.returncode)
        self.assertIn("must be non-empty text", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_fake_json_is_one_machine_readable_object(self) -> None:
        completed = run_cli("--fake", "--json")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("", completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual("cli-onegame", payload["command_id"])
        self.assertEqual("completed", payload["status"])
        self.assertEqual("completed", payload["reason_code"])
        self.assertEqual(1, payload["turns_played"])
        self.assertEqual(1, payload["actions_played"])
        self.assertEqual("result", payload["last_lobby_phase"])
        self.assertIsInstance(payload["evidence_ids"], list)
        self.assertIn("recommendation-turn", payload["evidence_ids"])
        self.assertIn("dispatch-end_turn-5", payload["evidence_ids"])
        self.assertIn("lobby-result", payload["evidence_ids"])


if __name__ == "__main__":
    unittest.main()
